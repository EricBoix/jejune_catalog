"""jejune_cli plugin for catalog roles.

Registers two roles and wires them into the jejune plugin system:

- deployment-catalog (abstract): deployment catalog checking, inherited by deployer.
- catalog-contributor: manages collection-level catalogs
  (full-catalog.yaml, deployments).

Command visibility:
  deployer / deployment-catalog         : catalog check-deployment
  catalog-contributor        : all catalog commands

Doc-steward manifest operations live in jejune_cli core (jejune manifest).

Module layout:
  _impl.py         — pure-Python business logic (no Click)
  _commands.py     — Click group and all subcommands
  _config_group.py — configuration init subgroup
  plugin.py        — role definitions, heuristic, plugin registration (this file)
"""

from jejune_cli.role_registry import ROLE_REGISTRY
from jejune_cli.plugin_description import plugin_description
from jejune_cli.plugin_role_description import plugin_role_description

from ._commands import catalog_group, convert_test
from ._config_group import curator_config_group
from ._impl import _check_availability

from jejune_cli.convert import convert as _convert_group
_convert_group.add_command(convert_test, "test")


def _is_catalog_contributor_cwd() -> bool:
    import subprocess
    from pathlib import Path
    cwd = Path.cwd()
    if not (cwd / "catalog.yaml").is_file():
        return False
    if not (cwd / ".git").is_dir():
        return False
    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd, stderr=subprocess.DEVNULL, text=True,
        ).strip()
        return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") == "jejune_catalog"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------

# Abstract role: never auto-detected; inherited by deployer to grant access to
# check-deployment. catalog-contributor is handled by an explicit allow
# in _commands._CatalogGroup, so it does NOT inherit deployment-catalog — this
# avoids a duplicate catalog section in catalog-contributor --help.
_deployment_catalog_role = plugin_role_description(
    name="deployment-catalog",
    components=frozenset({"catalog"}),
    includes=("contributor",),
    detection_reason="inherited by deployer",
    section_title="Deployment-catalog commands",
    detect=lambda: False,
    help_stage="collection",
    order=95,
    abstract=True,
    extend_includes={"deployer": ("deployment-catalog",)},
)
ROLE_REGISTRY.register_from_plugin(_deployment_catalog_role)
ROLE_REGISTRY.register_help_section("deployment-catalog", stage="collection", order=95)

catalog_role = plugin_role_description(
    name="catalog-contributor",
    components=frozenset({"catalog"}),
    includes=("contributor",),
    detection_reason="full-catalog.yaml detected",
    section_title="Catalog-contributor commands",
    detect=_is_catalog_contributor_cwd,
    help_stage="collection",
    order=20,
    config_group=curator_config_group,
)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

from jejune_cli.component_registry import REGISTRY as _REGISTRY
_catalog_comp = _REGISTRY.get("catalog")
if _catalog_comp is not None:
    _catalog_comp.repos = [("jejune_catalog", None, None)]

plugin = plugin_description(
    name="catalog",
    group=catalog_group,
    avail_hint="check network — jejune_catalog is a public repo and cloned automatically",
    check_availability=_check_availability,
    stage="collection",
    role=catalog_role,
)
