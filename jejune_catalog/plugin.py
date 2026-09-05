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

from jejune_cli.role import register_role_repos
from jejune_cli.plugin import JejunePlugin, JejuneRole
from jejune_cli.role import register_role as _register_role, register_role_help_section as _register_role_help_section

from ._commands import catalog_group, convert_test
from ._config_group import curator_config_group
from ._impl import _check_availability, _detect_catalog_contributor

from jejune_cli.convert import convert as _convert_group
_convert_group.add_command(convert_test, "test")


# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------

# Abstract role: never auto-detected; inherited by deployer to grant access to
# check-deployment. catalog-contributor is handled by an explicit allow
# in _commands._CatalogGroup, so it does NOT inherit deployment-catalog — this
# avoids a duplicate catalog section in catalog-contributor --help.
_deployment_catalog_role = JejuneRole(
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
_register_role(_deployment_catalog_role)
_register_role_help_section("deployment-catalog", stage="collection", order=95)

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
)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

register_role_repos("catalog-contributor", [("jejune_catalog", None, None)])

plugin = JejunePlugin(
    name="catalog",
    group=catalog_group,
    avail_hint="check network — jejune_catalog is a public repo and cloned automatically",
    check_availability=_check_availability,
    stage="collection",
    role=catalog_role,
)
