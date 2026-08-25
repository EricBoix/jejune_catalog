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


def _check_availability() -> tuple[bool, str]:
    """Return (ok, msg) for catalog availability.

    Tier 1: full-catalog.yaml present under JEJUNE_ROOT_DIR.
    Tier 2: already cloned into .jejune/tmp/.
    Tier 3: shallow-clone the public repo into .jejune/tmp/.
    Error only when both a local copy and the clone attempt fail.
    """
    from jejune_cli._ecosystem import REPO_ROOT_DIR

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


def _detect_collection_catalog_contributor() -> bool:
    try:
        return Path.cwd().joinpath("full-catalog.yaml").exists()
    except Exception:
        return False


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
    for doc in docs:
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
    root_dir: Path | None,
) -> list[tuple[str, bool, str]]:
    """Validate a deployment directory; return (item, ok, message)."""
    results: list[tuple[str, bool, str]] = []

    for fname in ("catalog.yaml", "deployment.env"):
        f = deployment_path / fname
        results.append((fname, f.exists(), "ok" if f.exists() else "missing"))

    catalog_path = deployment_path / "catalog.yaml"
    if not catalog_path.exists():
        return results

    ref_docs: dict[str, dict] = {}
    if catalog_ref.exists():
        for doc in yaml.safe_load(catalog_ref.read_text()).get("documents", []):
            ref_docs[doc["name"]] = doc

    for doc in yaml.safe_load(catalog_path.read_text()).get("documents", []):
        name = doc["name"]
        url = doc["url"].rstrip("/")
        issues: list[str] = []

        if root_dir is None:
            issues.append("JEJUNE_ROOT_DIR not set")
        elif not (root_dir / name).is_dir():
            issues.append(f"not cloned under {root_dir}")

        if name in ref_docs:
            ref_url = ref_docs[name]["url"].rstrip("/")
            if url != ref_url:
                issues.append(f"URL drift: deployment={url!r}, reference={ref_url!r}")

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
