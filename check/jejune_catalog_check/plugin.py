"""jejune_cli plugin for the Catalog Curator role.

Registers the catalog-contributor role and provides catalog management commands:
check/sync/check-deployment (collection-level), test/sample (basic utilities),
and a configuration init subcommand.
"""

import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

import click
import yaml

from jejune_cli.plugin import JejunePlugin, JejuneRole
from jejune_cli._env import dot_jejune
from jejune_cli.configuration import component_config_check
from jejune_cli.ecosystem import register_role_repos


_PLACEHOLDER = "_CHANGE_ME"
_CONFIG_VAR = "JEJUNE_ROOT_DIR"


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _catalog_config_status() -> tuple[str, str]:
    """Return (status, raw_msg) for catalog configuration."""
    val = os.environ.get("JEJUNE_ROOT_DIR")
    if not val:
        return "warn", "JEJUNE_ROOT_DIR not configured"
    if _PLACEHOLDER in val:
        return "error", "JEJUNE_ROOT_DIR has placeholder value"
    return "ok", ""


def _check_availability() -> tuple[bool, str]:
    status, msg = _catalog_config_status()
    if status == "error":
        return False, msg
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False, "gh CLI not authenticated"
    except FileNotFoundError:
        return False, "gh CLI not found"
    except subprocess.TimeoutExpired:
        return False, "gh auth status timed out"
    return True, "gh CLI authenticated"


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
) -> list[tuple[str, bool, str]]:
    """Scan JEJUNE_ROOT_DIR for jejune_doc_* repos and compare against catalog."""
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

    if do_add and to_add and catalog.exists():
        with catalog.open("a") as f:
            for name, url in to_add:
                f.write(f"  - name: {name}\n")
                f.write(f"    url: {url}\n")
                f.write(f"    public: true\n")
        click.echo(f"Added {len(to_add)} repo(s) to {catalog}.")

    return results


# ---------------------------------------------------------------------------
# Click command group
# ---------------------------------------------------------------------------

@click.group("catalog", short_help="Manage the document catalog")
def catalog_group():
    """Manage the catalog of jejune_doc_* repositories (collection-level)."""


@catalog_group.command("test")
@click.argument("catalog_file", required=False, default=None)
@click.option(
    "--root-dir",
    envvar="JEJUNE_ROOT_DIR",
    default=None,
    type=click.Path(),
    help="Directory holding side-by-side jejune_* clones (default: $JEJUNE_ROOT_DIR).",
)
@click.option(
    "--repo",
    default=None,
    help="Operate on this repository only (by name).",
)
@click.option(
    "--verbose", "-v",
    is_flag=True, default=False,
    help="Print referenced files for each document.",
)
def catalog_test(catalog_file, root_dir, repo, verbose):
    """Validate jejune_doc_* repositories found in the catalog.

    CATALOG_FILE defaults to $JEJUNE_CATALOG, then .jejune/catalog.yaml.

    Repositories are expected under ROOT_DIR/<name>/. Missing repositories
    (or all when ROOT_DIR is unset) are cloned into .jejune/tmp/ which is
    gitignored automatically.

    For each repository, catalog.yaml is parsed and every file it references is
    checked for existence. Exits with a non-zero status if any check fails.
    """
    from jejune_cli.test import _check_doc_yaml, _tmp_dir

    if catalog_file is None:
        catalog_file = os.environ.get("JEJUNE_CATALOG")
    if catalog_file is None:
        raise click.ClickException(
            "No catalog specified. Set $JEJUNE_CATALOG or pass CATALOG_FILE."
        )

    root = Path(root_dir) if root_dir else None
    if root is not None and not root.exists():
        source = (
            "$JEJUNE_ROOT_DIR"
            if os.environ.get("JEJUNE_ROOT_DIR") == root_dir
            else "--root-dir"
        )
        raise click.ClickException(f"ROOT_DIR ({source}) does not exist: {root}")

    catalog_path = Path(catalog_file)
    if not catalog_path.exists():
        raise click.ClickException(f"Catalog file not found: {catalog_file}")
    docs = yaml.safe_load(catalog_path.read_text())["documents"]

    if repo:
        docs = [d for d in docs if d["name"] == repo]
        if not docs:
            raise click.ClickException(f"Repository '{repo}' not found in catalog.")

    tmp: Path | None = None
    all_ok = True

    for doc in docs:
        name = doc["name"]
        url = doc["url"]

        repo_dir = root / name if root is not None else None
        if repo_dir is None or not repo_dir.exists():
            if tmp is None:
                tmp = _tmp_dir()
            repo_dir = tmp / name
            if not repo_dir.exists():
                click.echo(f"Cloning {name} ...")
                subprocess.run(["git", "clone", url, str(repo_dir)], check=True)

        cloned_label = click.style("cloned", fg="green")
        errors, file_refs = _check_doc_yaml(repo_dir)
        if errors:
            all_ok = False
            doc_label = click.style("invalid", fg="red")
            click.echo(f"  {name:<40}  {cloned_label} / {doc_label}")
            if verbose:
                for err in errors:
                    click.echo(f"      {click.style(err, fg='red')}")
        else:
            doc_label = click.style("valid", fg="green")
            click.echo(f"  {name:<40}  {cloned_label} / {doc_label}")
            if verbose:
                key_width = max((len(k) for k, _ in file_refs), default=0)
                for key, rel in file_refs:
                    click.echo(f"      {key:<{key_width}}  {rel}")

    click.echo()
    if all_ok:
        click.echo(click.style(f"{len(docs)} repo(s) — all ok.", fg="green"))
    else:
        click.echo(click.style(f"{len(docs)} repo(s) — some checks failed.", fg="red"))
        sys.exit(1)


@catalog_group.command("sample")
def catalog_sample():
    """Copy the built-in catalog template to catalog.yaml in the current directory."""
    target = Path.cwd() / "catalog.yaml"
    if target.exists():
        click.echo(
            click.style(
                f"Warning: {target} already exists — not overwriting.",
                fg="yellow",
            ),
            err=True,
        )
        return
    pkg = resources.files("jejune_catalog_check") / "templates" / "trivial-catalog.yaml"
    shutil.copy(str(pkg), target)
    click.echo(f"Created {target}")


@catalog_group.command("status-config")
def status_config():
    """Show catalog configuration status (mirrors the doctor Config Status column)."""
    status, msg = _catalog_config_status()
    if status == "ok":
        click.echo(f"catalog: {click.style('ok', fg='green')}")
    elif status == "warn":
        click.echo(f"catalog: {click.style(msg, fg='yellow')}")
    else:
        click.echo(f"catalog: {click.style('error', fg='red')}")


@catalog_group.command("hint-config")
def hint_config():
    """Show how to fix catalog configuration."""
    status, msg = _catalog_config_status()
    if status == "ok":
        click.echo(click.style("catalog is configured", fg="green"))
        return
    click.echo("edit .jejune/env-config (set JEJUNE_ROOT_DIR)")


@catalog_group.command("status-availability")
def status_availability():
    """Show catalog availability status (mirrors the doctor Availability Status column)."""
    ok, msg = _check_availability()
    if ok:
        click.echo(f"catalog: {click.style('ok', fg='green')}")
    else:
        click.echo(f"catalog: {click.style('error', fg='red')}")


@catalog_group.command("hint-availability")
def hint_availability():
    """Show how to fix catalog availability."""
    ok, msg = _check_availability()
    if ok:
        click.echo(click.style("catalog is available", fg="green"))
    else:
        click.echo("run `gh auth login` to authenticate the GitHub CLI")


@catalog_group.command("check")
@click.option(
    "--catalog", "catalog_path",
    required=True, type=click.Path(),
    help="Path to catalog.yaml.",
)
@click.option(
    "--root-dir", envvar="JEJUNE_ROOT_DIR", default=None, type=click.Path(),
    help="Directory holding jejune_doc_* clones (default: $JEJUNE_ROOT_DIR).",
)
def check(catalog_path, root_dir):
    """Verify catalog.yaml against GitHub visibility and local clones."""
    cfg_status, hint = component_config_check("catalog")
    if cfg_status == "error":
        raise click.ClickException(f"not configured — {hint}")
    cat_path = Path(catalog_path)
    root = Path(root_dir) if root_dir else None
    results = _check_catalog_impl(cat_path, root)
    all_ok = True
    for name, ok, msg in results:
        status = click.style("ok", fg="green") if ok else click.style(msg, fg="red")
        click.echo(f"  {name:<45} {status}")
        if not ok:
            all_ok = False
    if not all_ok:
        raise SystemExit(1)


@catalog_group.command("sync")
@click.option(
    "--catalog", "catalog_path",
    required=True, type=click.Path(),
    help="Path to catalog.yaml.",
)
@click.option(
    "--root-dir", envvar="JEJUNE_ROOT_DIR", default=None, type=click.Path(),
    help="Directory holding jejune_doc_* clones (default: $JEJUNE_ROOT_DIR).",
)
@click.option("--add", "do_add", is_flag=True, default=False,
              help="Append missing public repos to catalog.yaml.")
def sync(catalog_path, root_dir, do_add):
    """Report public jejune_doc_* repos under JEJUNE_ROOT_DIR missing from catalog.yaml."""
    if not root_dir:
        raise click.ClickException(
            "JEJUNE_ROOT_DIR is not set. Use --root-dir or set the env var."
        )
    cat_path = Path(catalog_path)
    results = _sync_catalog_impl(cat_path, Path(root_dir), do_add)
    for name, ok, msg in results:
        if ok:
            status = click.style(msg, fg="green")
        else:
            status = click.style(msg, fg="yellow" if "private" in msg else "red")
        click.echo(f"  {name:<45} {status}")


@catalog_group.command("check-deployment")
@click.argument("deployment_path", type=click.Path(exists=True))
@click.option(
    "--root-dir", envvar="JEJUNE_ROOT_DIR", default=None, type=click.Path(),
    help="Directory holding jejune_doc_* clones (default: $JEJUNE_ROOT_DIR).",
)
def check_deployment(deployment_path, root_dir):
    """Validate a deployment directory against full-catalog.yaml.

    DEPLOYMENT_PATH is the path to a jejune_deployments/deploy_*/ directory.
    The reference catalog is resolved from the sibling jejune_catalog/ repo.
    """
    dep_path = Path(deployment_path)
    root = Path(root_dir) if root_dir else None
    full_cat = dep_path.parent.parent / "jejune_catalog" / "full-catalog.yaml"
    results = _check_deployment_impl(dep_path, full_cat, root)
    all_ok = True
    for item, ok, msg in results:
        status = click.style(msg, fg="green") if ok else click.style(msg, fg="red")
        click.echo(f"  {item:<45} {status}")
        if not ok:
            all_ok = False
    if not all_ok:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Configuration init command and subgroup
# ---------------------------------------------------------------------------

_TEMPLATES = Path(__file__).parent / "templates"
_ECOSYSTEM_TEMPLATE = Path(__file__).parent / "templates" / "ecosystem-env-config"


@click.command("init")
def curator_init() -> None:
    """Write catalog-contributor scaffold files into .jejune/ in the current directory.

    Creates .jejune/role and .jejune/ecosystem-env-config from built-in templates.
    Adds .jejune to .gitignore so the whole directory stays local by default.
    """
    from jejune_cli.next_steps import print_next_steps

    d = dot_jejune()
    d.mkdir(exist_ok=True)

    created = []
    skipped = []
    for fname in ("role",):
        dst = d / fname
        if dst.exists():
            skipped.append(fname)
        else:
            shutil.copy2(_TEMPLATES / fname, dst)
            created.append(fname)

    eco_dst = d / "ecosystem-env-config"
    if eco_dst.exists():
        skipped.append("ecosystem-env-config")
    else:
        shutil.copy2(_ECOSYSTEM_TEMPLATE, eco_dst)
        created.append("ecosystem-env-config")

    for f in created:
        click.echo(click.style(f"  created  .jejune/{f}", fg="green"))
    for f in skipped:
        click.echo(click.style(f"  skipped  .jejune/{f} (already exists)", fg="yellow"))

    gitignore = Path.cwd() / ".gitignore"
    if not gitignore.exists() or ".jejune" not in gitignore.read_text().splitlines():
        with gitignore.open("a") as fh:
            fh.write(".jejune\n")
        click.echo(click.style("  updated  .gitignore (.jejune)", fg="green"))

    print_next_steps()


@click.group("catalog-contributor", short_help="Catalog-contributor role workspace")
def curator_config_group():
    """Initialise and inspect the catalog-contributor workspace."""


curator_config_group.add_command(curator_init, "init")


# ---------------------------------------------------------------------------
# Role definition
# ---------------------------------------------------------------------------

def _detect_catalog_contributor() -> bool:
    try:
        return Path.cwd().joinpath("full-catalog.yaml").exists()
    except Exception:
        return False


catalog_role = JejuneRole(
    name="catalog-contributor",
    components=frozenset({"catalog"}),
    includes=("contributor",),
    detection_reason="full-catalog.yaml detected",
    section_title="Catalog-contributor commands",
    detect=_detect_catalog_contributor,
    help_stage="collection",
    order=20,
    config_group=curator_config_group,
    extend_includes={"doc-steward": ("catalog-contributor",)},
)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

register_role_repos("catalog-contributor", [("jejune_catalog", None, None)])

plugin = JejunePlugin(
    name="catalog",
    group=catalog_group,
    config_vars=[_CONFIG_VAR],
    config_hint="edit .jejune/env-config (set JEJUNE_ROOT_DIR)",
    avail_hint="run `gh auth login` to authenticate the GitHub CLI",
    check_availability=_check_availability,
    stage="collection",
    role=catalog_role,
)
