"""Click command group and all catalog subcommands.

Role-aware gating (two tiers):
- _DEPLOYMENT_CATALOG_ONLY : visible/runnable for deployer (deployment-catalog role)
  and collection-catalog-contributor.
- _COLLECTION_ONLY         : visible/runnable for collection-catalog-contributor only.
"""

import os
import re
import sys
from importlib import resources
from pathlib import Path

import click
import yaml

from jejune_cli.configuration import component_config_check
from jejune_cli.role import detect_role as _detect_role, role_inherits as _role_inherits

from ._impl import (
    _catalog_config_status,
    _check_availability,
    _check_catalog_impl,
    _check_deployment_impl,
    _sync_catalog_impl,
)


# Commands accessible to deployer (via deployment-catalog role) AND collection role.
_DEPLOYMENT_CATALOG_ONLY: frozenset[str] = frozenset({"check-deployment"})

# Commands accessible to collection-catalog-contributor only.
_COLLECTION_ONLY: frozenset[str] = frozenset({
    "sync", "test", "sample",
    "status-config", "hint-config", "status-availability", "hint-availability",
})

_COLLECTION_ROLE = "collection-catalog-contributor"
_DEPLOYMENT_CATALOG_ROLE = "deployment-catalog"


# ---------------------------------------------------------------------------
# Role-aware group
# ---------------------------------------------------------------------------

class _CatalogGroup(click.Group):
    """Hides and blocks role-gated commands based on the active role."""

    def _is_collection_role(self) -> bool:
        active_role, _ = _detect_role()
        return active_role == _COLLECTION_ROLE

    def _is_deployment_catalog_role(self) -> bool:
        """True for deployer (inherits deployment-catalog) and collection role."""
        active_role, _ = _detect_role()
        return (
            _role_inherits(active_role, _DEPLOYMENT_CATALOG_ROLE)
            or active_role == _COLLECTION_ROLE
        )

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        is_collection = self._is_collection_role()
        is_deployment_catalog = self._is_deployment_catalog_role()
        commands = []
        for name in self.list_commands(ctx):
            if not is_collection and name in _COLLECTION_ONLY:
                continue
            if not is_deployment_catalog and name in _DEPLOYMENT_CATALOG_ONLY:
                continue
            cmd = self.get_command(ctx, name)
            if cmd and not cmd.hidden:
                commands.append((name, cmd))
        if commands:
            with formatter.section("Commands"):
                formatter.write_dl(
                    [(name, cmd.get_short_help_str(limit=formatter.width))
                     for name, cmd in commands]
                )

    def invoke(self, ctx: click.Context) -> object:
        cmd_name = ctx._protected_args[0] if ctx._protected_args else None
        if cmd_name in _COLLECTION_ONLY and not self._is_collection_role():
            raise click.ClickException(
                f"'{cmd_name}' is only available for the {_COLLECTION_ROLE} role."
            )
        if cmd_name in _DEPLOYMENT_CATALOG_ONLY and not self._is_deployment_catalog_role():
            raise click.ClickException(
                f"'{cmd_name}' requires the {_DEPLOYMENT_CATALOG_ROLE} role "
                f"(or {_COLLECTION_ROLE})."
            )
        return super().invoke(ctx)


@click.group("catalog", cls=_CatalogGroup, short_help="Manage the document catalog")
def catalog_group():
    """Manage the catalog of jejune_doc_* repositories."""


# ---------------------------------------------------------------------------
# Doc-level command (available to all catalog roles)
# ---------------------------------------------------------------------------

@catalog_group.command("check")
@click.option(
    "--catalog", "catalog_path",
    required=False, default=None, type=click.Path(),
    help="Collection catalog path. If omitted, validates the current directory's catalog.yaml.",
)
@click.option(
    "--root-dir", envvar="JEJUNE_ROOT_DIR", default=None, type=click.Path(),
    help="Directory holding jejune_doc_* clones (default: $JEJUNE_ROOT_DIR).",
)
@click.option(
    "--verbose", "-v", is_flag=True, default=False,
    help="Print file references (doc-level mode only).",
)
def check(catalog_path, root_dir, verbose):
    """Validate a catalog.

    Without --catalog: validates the current directory's catalog.yaml (doc-level).
    With --catalog PATH: verifies a collection catalog against GitHub visibility and
    local clones (collection-catalog-contributor only).
    """
    if catalog_path is None:
        _doc_yaml = Path.cwd() / "catalog.yaml"
        _is_deployment_catalog = (
            _doc_yaml.exists()
            and "documents" in (yaml.safe_load(_doc_yaml.read_text()) or {})
        )
        if _is_deployment_catalog:
            # Deployment-catalog mode: auto-dispatch to the deployment check.
            dep_path = Path.cwd()
            full_cat = dep_path.parent.parent / "jejune_catalog" / "full-catalog.yaml"
            results = _check_deployment_impl(dep_path, full_cat)
            all_ok = True
            for item, ok, msg in results:
                status = click.style(msg, fg="green") if ok else click.style(msg, fg="red")
                click.echo(f"  {item:<45} {status}")
                if not ok:
                    all_ok = False
            if not all_ok:
                sys.exit(1)
        else:
            # Doc-level mode: validate the current repo's catalog.yaml.
            from jejune_cli.test import _check_doc_yaml
            errors, file_refs = _check_doc_yaml(Path.cwd())
            if errors:
                for err in errors:
                    click.echo(f"  {click.style(err, fg='red')}")
                n = len([e for e in errors if "see " not in e])
                click.echo(click.style(f"catalog.yaml — {n} error(s)", fg="red"))
                sys.exit(1)
            else:
                if verbose and file_refs:
                    key_width = max(len(k) for k, _ in file_refs)
                    for key, rel in file_refs:
                        click.echo(f"  {key:<{key_width}}  {rel}")
                click.echo(click.style("catalog.yaml — ok", fg="green"))
    else:
        # Collection-level mode: check each catalog entry.
        active_role, _ = _detect_role()
        if active_role != _COLLECTION_ROLE:
            raise click.ClickException(
                f"--catalog is only available for the {_COLLECTION_ROLE} role."
            )
        cfg_status, hint = component_config_check("catalog")
        if cfg_status == "error":
            raise click.ClickException(f"not configured — {hint}")
        cat_path = Path(catalog_path)
        root = Path(root_dir) if root_dir else None
        results = _check_catalog_impl(cat_path, root)
        if not results:
            click.echo(click.style("no documents found in catalog — wrong file?", fg="yellow"))
            return
        all_ok = True
        for name, ok, msg in results:
            status = click.style("ok", fg="green") if ok else click.style(msg, fg="red")
            click.echo(f"  {name:<45} {status}")
            if not ok:
                all_ok = False
        if all_ok:
            click.echo(click.style(f"{len(results)} document(s) — all ok.", fg="green"))
        else:
            sys.exit(1)


# ---------------------------------------------------------------------------
# Collection-level commands (collection-catalog-contributor only)
# ---------------------------------------------------------------------------

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
    import subprocess
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
    pkg = resources.files("jejune_catalog") / "templates" / "trivial-catalog.yaml"
    import shutil
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
    results, n_added = _sync_catalog_impl(cat_path, Path(root_dir), do_add)
    for name, ok, msg in results:
        if ok:
            status = click.style(msg, fg="green")
        else:
            status = click.style(msg, fg="yellow" if "private" in msg else "red")
        click.echo(f"  {name:<45} {status}")
    if n_added:
        click.echo(f"Added {n_added} repo(s) to {cat_path}.")


@catalog_group.command("slug")
@click.argument("doc_catalog", type=click.Path(exists=True))
@click.option(
    "--full-catalog", "full_catalog_path",
    default=None, type=click.Path(),
    help="Path to full-catalog.yaml (default: $JEJUNE_ROOT_DIR/jejune_catalog/full-catalog.yaml).",
)
@click.option(
    "--root-dir", envvar="JEJUNE_ROOT_DIR", default=None, type=click.Path(),
    help="Root dir holding side-by-side jejune_* clones (default: $JEJUNE_ROOT_DIR).",
)
def slug(doc_catalog, full_catalog_path, root_dir):
    """Compute a unique doc_name slug from a doc_*/catalog.yaml.

    DOC_CATALOG is the path to the document's catalog.yaml.

    The slug is derived from the document's title (extended with author or isbn
    only when needed for uniqueness). The full-catalog.yaml is consulted to
    guarantee uniqueness. No files are written.
    """
    doc = yaml.safe_load(Path(doc_catalog).read_text())
    title = doc.get("title", "")
    authors = doc.get("authors", [])
    isbn = doc.get("isbn") or ""

    if full_catalog_path:
        full_cat = Path(full_catalog_path)
    elif root_dir:
        full_cat = Path(root_dir) / "jejune_catalog" / "full-catalog.yaml"
    else:
        full_cat = Path.cwd() / "full-catalog.yaml"

    existing: set[str] = set()
    if full_cat.exists():
        for entry in yaml.safe_load(full_cat.read_text()).get("documents", []):
            if "doc_name" in entry:
                existing.add(entry["doc_name"])

    def _slugify(text: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        return re.sub(r"-{2,}", "-", s)

    # Use main title only (before first colon or em-dash) for a shorter base slug.
    main_title = re.split(r"[:—]", title, maxsplit=1)[0].strip()
    first_author = authors[0] if authors else ""
    candidates = [
        _slugify(main_title),
        _slugify(f"{main_title} {first_author}") if first_author else None,
        _slugify(f"{main_title} {first_author} {isbn}") if first_author or isbn else None,
        _slugify(f"{title} {first_author} {isbn}"),  # full title fallback
    ]

    for candidate in candidates:
        if candidate and candidate not in existing:
            click.echo(candidate)
            return

    raise click.ClickException(
        "Could not generate a unique slug — all candidates already exist in full-catalog.yaml."
    )


@catalog_group.command("check-deployment")
@click.argument("deployment_path", type=click.Path(exists=True))
def check_deployment(deployment_path):
    """Validate a deployment directory against full-catalog.yaml.

    DEPLOYMENT_PATH is the path to a jejune_deployments/deploy_*/ directory.
    The reference catalog is resolved from the sibling jejune_catalog/ repo.
    """
    dep_path = Path(deployment_path)
    full_cat = dep_path.parent.parent / "jejune_catalog" / "full-catalog.yaml"
    results = _check_deployment_impl(dep_path, full_cat)
    all_ok = True
    for item, ok, msg in results:
        status = click.style(msg, fg="green") if ok else click.style(msg, fg="red")
        click.echo(f"  {item:<45} {status}")
        if not ok:
            all_ok = False
    if not all_ok:
        raise SystemExit(1)
