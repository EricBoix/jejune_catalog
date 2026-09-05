"""Pure-Python business logic for catalog operations.

No Click dependency — all functions are independently testable.
"""

import os
import subprocess
from pathlib import Path

import yaml

from jejune_cli._env import dot_jejune


_PLACEHOLDER = "_CHANGE_ME"
_CONFIG_VAR = "JEJUNE_ROOT_DIR"
_REPO_NAME = "jejune_catalog"

_CATALOG_SCHEMA_PATH = Path(__file__).parent / "schema" / "catalog.yaml"


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _catalog_config_status() -> tuple[str, str]:
    """Return (status, raw_msg) for catalog configuration."""
    val = os.environ.get(_CONFIG_VAR)
    if not val:
        return "warn", f"{_CONFIG_VAR} not configured"
    if _PLACEHOLDER in val:
        return "warn", f"{_CONFIG_VAR} has placeholder value"
    return "ok", ""


def _check_deployment_catalog_availability() -> tuple[bool, str]:
    """Return (ok, msg) for the deployment catalog.yaml in CWD."""
    cwd = Path.cwd()
    full_cat = cwd.parent.parent / _REPO_NAME / "full-catalog.yaml"
    results = _check_deployment_impl(cwd, full_cat)
    failing = [item for item, ok, _ in results if not ok]
    if not failing:
        return True, "deployment catalog ok"
    return False, "; ".join(failing)


def _check_availability() -> tuple[bool, str]:
    """Return (ok, msg) for catalog availability.

    For the deployer role (inherits deployment-catalog): validates the
    deployment catalog.yaml in CWD via _check_deployment_impl.

    For all other roles (catalog-contributor, doc-steward, …):
    Tier 1: full-catalog.yaml present under JEJUNE_ROOT_DIR.
    Tier 2: already cloned into .jejune/tmp/.
    Tier 3: shallow-clone the public repo into .jejune/tmp/.
    Error only when both a local copy and the clone attempt fail.
    """
    try:
        from jejune_cli.role import detect_role, role_inherits
        active_role, _ = detect_role()
        if role_inherits(active_role, "deployment-catalog"):
            return _check_deployment_catalog_availability()
    except Exception:
        pass

    from jejune_cli._git_server_config import REPO_ROOT_DIR

    raw_root = os.environ.get(_CONFIG_VAR, "")
    if raw_root and _PLACEHOLDER not in raw_root:
        if (Path(raw_root) / _REPO_NAME / "full-catalog.yaml").exists():
            return True, f"full-catalog.yaml found under {_CONFIG_VAR}"

    if (dot_jejune() / "tmp" / _REPO_NAME / "full-catalog.yaml").exists():
        return True, "full-catalog.yaml available via .jejune/tmp"

    clone_dest = dot_jejune() / "tmp" / _REPO_NAME
    try:
        clone_dest.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--depth=1",
             f"{REPO_ROOT_DIR}/{_REPO_NAME}", str(clone_dest)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return True, f"cloned {_REPO_NAME} into .jejune/tmp"
    except subprocess.TimeoutExpired:
        pass

    return False, f"could not access {_REPO_NAME} locally or via git clone"


def _detect_catalog_contributor() -> bool:
    try:
        return Path.cwd().joinpath("full-catalog.yaml").exists()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Deployment catalog schema validation
# ---------------------------------------------------------------------------

def _validate_catalog_entry(doc: object, index: int) -> list[str]:
    """Validate one entry against the deployment catalog schema; return error strings."""
    if not isinstance(doc, dict):
        return [f"entry #{index}: not a mapping"]
    schema = yaml.safe_load(_CATALOG_SCHEMA_PATH.read_text())
    errors: list[str] = []
    _TYPE_MAP = {"string": str, "boolean": bool}
    for field, ftype in schema.get("required_fields", {}).items():
        if field not in doc:
            errors.append(f"required field '{field}' missing")
        elif not isinstance(doc[field], _TYPE_MAP.get(ftype, object)):
            errors.append(f"'{field}' must be a {ftype}")
    for field, ftype in schema.get("optional_fields", {}).items():
        if field in doc and not isinstance(doc[field], _TYPE_MAP.get(ftype, object)):
            errors.append(f"'{field}' must be a {ftype}")
    if errors:
        errors.append(f"see {_CATALOG_SCHEMA_PATH} for the expected format")
    return errors


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _gh_is_private(slug: str) -> tuple[bool | None, str]:
    """Query GitHub via gh CLI; return (is_private, error_message)."""
    try:
        result = subprocess.run(
            ["gh", "repo", "view", slug, "--json", "isPrivate", "--jq", ".isPrivate"],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        return None, "gh CLI not found"
    except subprocess.TimeoutExpired:
        return None, "gh query timed out"
    if result.returncode != 0:
        return None, result.stderr.strip() or "gh query failed"
    return result.stdout.strip() == "true", ""


# ---------------------------------------------------------------------------
# Catalog operation implementations
# ---------------------------------------------------------------------------

def _check_catalog_impl(catalog: Path, root_dir: Path | None) -> list[tuple[str, bool, str]]:
    """Check each catalog entry for visibility and local clone; return (name, ok, message)."""
    if not catalog.exists():
        return [("catalog.yaml", False, f"not found: {catalog}")]
    docs = yaml.safe_load(catalog.read_text()).get("documents", [])
    results: list[tuple[str, bool, str]] = []
    for i, doc in enumerate(docs):
        schema_errors = _validate_catalog_entry(doc, i)
        if schema_errors:
            label = doc.get("name") if isinstance(doc, dict) else None
            results.append((label or f"entry #{i}", False, "; ".join(schema_errors)))
            continue
        name = doc["name"]
        url = doc["url"].rstrip("/")
        expected_public = doc.get("public", True)
        issues: list[str] = []

        if root_dir is None:
            issues.append("JEJUNE_ROOT_DIR not set")
        elif not (root_dir / name).is_dir():
            issues.append(f"not cloned under {root_dir}")

        parts = url.split("/")
        if len(parts) >= 2:
            slug = f"{parts[-2]}/{parts[-1]}"
            is_private, err = _gh_is_private(slug)
            if err:
                issues.append(err)
            else:
                actual_public = not is_private
                if actual_public != expected_public:
                    catalog_val = "public" if expected_public else "private"
                    github_val = "public" if actual_public else "private"
                    issues.append(
                        f"visibility mismatch: catalog={catalog_val}, GitHub={github_val}"
                    )

        results.append((name, not issues, "; ".join(issues) if issues else "ok"))
    return results


def _check_deployment_impl(
    deployment_path: Path,
    catalog_ref: Path,
) -> list[tuple[str, bool, str]]:
    """Validate a deployment catalog; return (item, ok, message)."""
    results: list[tuple[str, bool, str]] = []

    f = deployment_path / "catalog.yaml"
    results.append(("catalog.yaml", f.exists(), "ok" if f.exists() else "missing"))

    catalog_path = deployment_path / "catalog.yaml"
    if not catalog_path.exists():
        return results

    ref_docs: dict[str, dict] = {}
    if catalog_ref.exists():
        for doc in yaml.safe_load(catalog_ref.read_text()).get("documents", []):
            if isinstance(doc, dict) and "name" in doc:
                ref_docs[doc["name"]] = doc

    for i, doc in enumerate(yaml.safe_load(catalog_path.read_text()).get("documents", [])):
        schema_errors = _validate_catalog_entry(doc, i)
        if schema_errors:
            label = doc.get("name") if isinstance(doc, dict) else None
            results.append((label or f"entry #{i}", False, "; ".join(schema_errors)))
            continue
        name = doc["name"]
        url = doc["url"].rstrip("/")
        issues: list[str] = []

        if name in ref_docs:
            ref_url = ref_docs[name]["url"].rstrip("/")
            if url != ref_url:
                issues.append(f"URL drift: deployment={url!r}, reference={ref_url!r}")
        elif ref_docs:
            issues.append("not found in reference catalog")

        label = "public" if doc.get("public") else "private"
        results.append((
            name,
            not issues,
            f"ok ({label})" if not issues else "; ".join(issues),
        ))

    return results


def _sync_catalog_impl(
    catalog: Path,
    root_dir: Path,
    do_add: bool,
) -> tuple[list[tuple[str, bool, str]], int]:
    """Scan JEJUNE_ROOT_DIR for jejune_doc_* repos and compare against catalog.

    Returns (results, n_added) where n_added is the count of repos appended to
    catalog when do_add is True.
    """
    existing: set[str] = set()
    if catalog.exists():
        for doc in yaml.safe_load(catalog.read_text()).get("documents", []):
            existing.add(doc["name"])

    results: list[tuple[str, bool, str]] = []
    to_add: list[tuple[str, str]] = []

    for repo_dir in sorted(root_dir.glob("jejune_doc_*")):
        if not repo_dir.is_dir():
            continue
        name = repo_dir.name

        if name in existing:
            results.append((name, True, "already in catalog"))
            continue

        remote = subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
            capture_output=True, text=True,
        )
        if remote.returncode != 0:
            results.append((name, False, "no git remote"))
            continue

        url = remote.stdout.strip().removesuffix(".git")
        parts = url.split("/")
        slug = f"{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else ""

        if not slug:
            results.append((name, False, f"unexpected remote URL: {url}"))
            continue

        is_private, err = _gh_is_private(slug)
        if err:
            results.append((name, False, err))
        elif is_private:
            results.append((name, True, "private — add manually to deployment catalog if needed"))
        else:
            results.append((name, False, "public repo missing from catalog"))
            to_add.append((name, url))

    n_added = 0
    if do_add and to_add and catalog.exists():
        with catalog.open("a") as f:
            for name, url in to_add:
                f.write(f"  - name: {name}\n")
                f.write(f"    url: {url}\n")
                f.write(f"    public: true\n")
        n_added = len(to_add)

    return results, n_added


# ---------------------------------------------------------------------------
# Catalog document iteration (shared by convert-test and future commands)
# ---------------------------------------------------------------------------

def _iter_docs(docs, root, eco_tmp):
    """Yield (name, url, repo_dir) for each catalog entry.

    Resolution order: JEJUNE_ROOT_DIR → .jejune/tmp → clone into .jejune/tmp.
    Resolution order: JEJUNE_ROOT_DIR → .jejune/tmp → clone into .jejune/tmp.
    """
    from jejune_cli.component_registry import REGISTRY
    from jejune_cli.test import _tmp_dir

    eco = REGISTRY.get("ecosystem")
    tmp = None
    for doc in docs:
        name, url = doc["name"], doc["url"]
        tier, base = eco.repo_status(name, root, eco_tmp)
        if tier in ("root", "tmp"):
            repo_dir = Path(base)
        else:
            if tmp is None:
                tmp = _tmp_dir()
            repo_dir = tmp / name
            if not repo_dir.exists():
                print(f"Cloning {name} ...")
                subprocess.run(["git", "clone", url, str(repo_dir)], check=True)
        yield name, url, repo_dir


# ---------------------------------------------------------------------------
# Per-document converter build and test logic
# ---------------------------------------------------------------------------

_DOC_PREFIX = "jejune_doc_"


def _docker_image_name(repo_dir: Path) -> str:
    name = repo_dir.resolve().name
    if name.startswith(_DOC_PREFIX):
        name = name[len(_DOC_PREFIX):]
    return f"jejune:convert_{name}" if name else "jejune:convert"


def _has_converter(repo_dir: Path) -> tuple[bool, Path, Path]:
    """Return (has_converter, dockerfile, context)."""
    ctx = repo_dir / "DockerContext"
    df = ctx / "Dockerfile"
    return (ctx.is_dir() and df.exists()), df, ctx


def _convert_test_doc(
    repo_dir: Path, no_cache: bool, no_build: bool
) -> tuple[str, str]:
    """Return (outcome, detail). outcome ∈ {skipped, build_failed, unchanged, changed}."""
    has, dockerfile, context = _has_converter(repo_dir)
    if not has:
        return "skipped", "no DockerContext"
    image = _docker_image_name(repo_dir)
    if not no_build:
        extra = ["--no-cache"] if no_cache else []
        r = subprocess.run(
            ["docker", "build", *extra, "-t", image, "-f", str(dockerfile), str(context)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()
            return "build_failed", tail[-1] if tail else "build failed"
    else:
        r = subprocess.run(
            ["docker", "images", "-q", image], capture_output=True, text=True
        )
        if not (r.returncode == 0 and r.stdout.strip()):
            return "build_failed", f"image {image!r} not found"
    r = subprocess.run(
        ["docker", "run", "--rm", image, "--test"], capture_output=True, text=True
    )
    return ("unchanged", "tests passed") if r.returncode == 0 else ("changed", "tests failed")
