"""jejune_cli plugin for the Catalog Curator role.

Exposes catalog check/sync/check-deployment commands and a doctor
availability check (gh CLI reachable + catalog.yaml present).
"""

import os
import subprocess
from pathlib import Path

import click

from jejune_cli.plugin import JejunePlugin
from jejune_cli.catalog import (
    _catalog_config_status,
    _check_catalog_impl,
    _check_deployment_impl,
    _sync_catalog_impl,
)
from jejune_cli._env import dot_jejune
from jejune_cli.configuration import component_config_check


_CONFIG_VAR = "JEJUNE_ROOT_DIR"


def _check_availability() -> tuple[bool, str]:
    # Basic config gate.
    status, msg = _catalog_config_status()
    if status == "error":
        return False, msg
    # GitHub CLI reachability.
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
# Click group
# ---------------------------------------------------------------------------

@click.group("catalog", short_help="Manage the document catalog")
def catalog_group():
    """Manage the catalog of jj_doc_* repositories (collection-level)."""


@catalog_group.command("status-config")
def status_config():
    """Show catalog configuration status (mirrors the doctor Config Status column)."""
    from jejune_cli.configuration import check_config_group
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
    env_issue = "JEJUNE_ROOT_DIR" in msg
    cat_issue = "catalog.yaml" in msg
    if env_issue and cat_issue:
        click.echo("edit .jejune/env-config and .jejune/catalog.yaml")
    elif env_issue:
        click.echo("edit .jejune/env-config (set JEJUNE_ROOT_DIR)")
    else:
        click.echo("edit .jejune/catalog.yaml")


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
    default=None, type=click.Path(),
    help="Path to catalog.yaml (default: .jejune/catalog.yaml).",
)
@click.option(
    "--root-dir", envvar="JEJUNE_ROOT_DIR", default=None, type=click.Path(),
    help="Directory holding jj_* clones (default: $JEJUNE_ROOT_DIR).",
)
def check(catalog_path, root_dir):
    """Verify catalog.yaml against GitHub visibility and local clones."""
    cfg_status, hint = component_config_check("catalog")
    if cfg_status == "error":
        raise click.ClickException(f"not configured — {hint}")
    cat_path = Path(catalog_path) if catalog_path else dot_jejune() / "catalog.yaml"
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
    default=None, type=click.Path(),
    help="Path to catalog.yaml (default: .jejune/catalog.yaml).",
)
@click.option(
    "--root-dir", envvar="JEJUNE_ROOT_DIR", default=None, type=click.Path(),
    help="Directory holding jj_* clones (default: $JEJUNE_ROOT_DIR).",
)
@click.option("--add", "do_add", is_flag=True, default=False,
              help="Append missing public repos to catalog.yaml.")
def sync(catalog_path, root_dir, do_add):
    """Report public jj_doc_* repos under JEJUNE_ROOT_DIR missing from catalog.yaml."""
    if not root_dir:
        raise click.ClickException("JEJUNE_ROOT_DIR is not set. Use --root-dir or set the env var.")
    cat_path = Path(catalog_path) if catalog_path else dot_jejune() / "catalog.yaml"
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
    help="Directory holding jj_* clones (default: $JEJUNE_ROOT_DIR).",
)
def check_deployment(deployment_path, root_dir):
    """Validate a deployment directory against catalog.yaml.

    DEPLOYMENT_PATH is the path to a jj_deployments/deploy_*/ directory.
    """
    dep_path = Path(deployment_path)
    root = Path(root_dir) if root_dir else None
    results = _check_deployment_impl(dep_path, dot_jejune() / "catalog.yaml", root)
    all_ok = True
    for item, ok, msg in results:
        status = click.style(msg, fg="green") if ok else click.style(msg, fg="red")
        click.echo(f"  {item:<45} {status}")
        if not ok:
            all_ok = False
    if not all_ok:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

plugin = JejunePlugin(
    name="catalog",
    group=catalog_group,
    config_vars=[_CONFIG_VAR],
    config_hint="edit .jejune/env-config or .jejune/catalog.yaml",
    avail_hint="run `gh auth login` to authenticate the GitHub CLI",
    check_availability=_check_availability,
    stage="collection",
)
