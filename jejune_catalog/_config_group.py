"""Configuration init subgroup for the collection-catalog-contributor role.

Provides `jejune configuration collection-catalog-contributor init`, which
scaffolds the workspace files (.jejune/role, .jejune/ecosystem-env-config).
"""

import shutil
from pathlib import Path

import click

from jejune_cli._env import dot_jejune


_TEMPLATES = Path(__file__).parent / "templates"
_ECOSYSTEM_TEMPLATE = Path(__file__).parent / "templates" / "ecosystem-env-config"


@click.command("init")
def curator_init() -> None:
    """Write collection-catalog-contributor scaffold files into .jejune/ in the current directory.

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


@click.group("collection-catalog-contributor", short_help="Collection-catalog-contributor role workspace")
def curator_config_group():
    """Initialise and inspect the collection-catalog-contributor workspace."""


curator_config_group.add_command(curator_init, "init")
