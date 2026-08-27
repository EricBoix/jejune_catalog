"""jejune_cli plugin for catalog roles.

Registers three roles and wires them into the jejune plugin system:

- doc-catalog-contributor (abstract): doc-level catalog.yaml validation,
  inherited by doc-steward and collection-catalog-contributor.
- deployment-catalog (abstract): deployment catalog checking, inherited by deployer.
- collection-catalog-contributor: manages collection-level catalogs
  (full-catalog.yaml, deployments). Replaces the former catalog-contributor role.

Command visibility:
  doc-steward / doc-catalog-contributor : catalog check (doc-level)
  deployer / deployment-catalog         : catalog check-deployment
  collection-catalog-contributor        : all catalog commands

Module layout:
  _impl.py         — pure-Python business logic (no Click)
  _commands.py     — Click group and all subcommands
  _config_group.py — configuration init subgroup
  plugin.py        — role definitions, heuristic, plugin registration (this file)
"""

from pathlib import Path

from jejune_cli.ecosystem import register_role_repos
from jejune_cli.next_steps import HeuristicStep, register_heuristic
from jejune_cli.plugin import JejunePlugin, JejuneRole
from jejune_cli.role import register_role as _register_role, register_role_help_section as _register_role_help_section

from ._commands import catalog_group
from ._config_group import curator_config_group
from ._impl import _check_availability, _detect_collection_catalog_contributor


# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------

# Abstract role: never auto-detected; inherited by doc-steward and
# collection-catalog-contributor to share doc-level catalog commands.
_doc_catalog_contributor_role = JejuneRole(
    name="doc-catalog-contributor",
    components=frozenset({"catalog"}),
    includes=("contributor",),
    detection_reason="inherited by doc-steward and collection-catalog-contributor",
    section_title="Doc-catalog-contributor commands",
    detect=lambda: False,
    help_stage="single-document",
    order=15,
    abstract=True,
    extend_includes={"doc-steward": ("doc-catalog-contributor",)},
)
_register_role(_doc_catalog_contributor_role)

# Abstract role: never auto-detected; inherited by deployer to grant access to
# check-deployment. collection-catalog-contributor is handled by an explicit allow
# in _commands._CatalogGroup, so it does NOT inherit deployment-catalog — this
# avoids a duplicate catalog section in collection-catalog-contributor --help.
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
    name="collection-catalog-contributor",
    components=frozenset({"catalog"}),
    includes=("doc-catalog-contributor",),
    detection_reason="full-catalog.yaml detected",
    section_title="Collection-catalog-contributor commands",
    detect=_detect_collection_catalog_contributor,
    help_stage="collection",
    order=20,
    config_group=curator_config_group,
)


# ---------------------------------------------------------------------------
# Heuristic registration
# ---------------------------------------------------------------------------

def _doc_catalog_has_errors() -> bool:
    try:
        import yaml
        from jejune_cli.test import _check_doc_yaml
        doc_yaml = Path.cwd() / "catalog.yaml"
        if doc_yaml.exists() and "documents" in (yaml.safe_load(doc_yaml.read_text()) or {}):
            return False  # deployment catalog, not a doc catalog
        errors, _ = _check_doc_yaml(Path.cwd())
        return bool(errors)
    except Exception:
        return False


def _catalog_is_available() -> bool:
    ok, _ = _check_availability()
    return ok


register_heuristic(
    HeuristicStep(
        label="Fix document catalog",
        command="jejune catalog check",
        conditions=[_doc_catalog_has_errors],
        anti_conditions=[_catalog_is_available],
    ),
    roles={"doc-steward", "doc-catalog-contributor", "collection-catalog-contributor"},
)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

register_role_repos("collection-catalog-contributor", [("jejune_catalog", None, None)])

plugin = JejunePlugin(
    name="catalog",
    group=catalog_group,
    avail_hint="check network — jejune_catalog is a public repo and cloned automatically",
    check_availability=_check_availability,
    stage="collection",
    role=catalog_role,
)
