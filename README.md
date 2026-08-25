# jejune\_catalog

Home of the Catalog Curator role in the jejune ecosystem.

## Contents

| Path | Purpose |
|------|---------|
| `full-catalog.yaml` | Canonical reference of all known `jj_doc_*` repositories |
| `Doc/` | Curator working space (notes, steward communications) |
| `jejune_catalog/` | `jejune_cli` plugin providing catalog commands and health check |

## full-catalog.yaml

Single source of truth for the collection. Every deployment catalog must be a
validated subset of this file. Maintained exclusively by the Catalog Curator.

Fields per entry: `name`, `url`, `public`.

## jejune\_cli plugin

Install once (requires `jejune_cli`):

```sh
pip install -e .
```

This registers the `catalog` command group with `jejune`:

```sh
jejune catalog check              # validate entries against GitHub + local clones
jejune catalog sync               # find unregistered jj_doc_* repos
jejune catalog check-deployment /path/to/deploy_name
```

Run standalone (without going through `jejune`):

```sh
python -m jejune_catalog check
```

Once installed, `jejune doctor` reports catalog health when run from a
`full-catalog.yaml` directory (Catalog Curator role auto-detected).
