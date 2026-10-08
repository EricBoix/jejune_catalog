# jejune\_catalog

Home of the Catalog Curator role in the jejune ecosystem.

## Introduction

The central role of this repository is to hold the `catalog.yaml` file that is the single source of truth for the jejune document collection (`jejune_doc_*` repositories).
The `catalog.yaml` is maintained exclusively by the [`Catalog Curator`](https://github.com/EricBoix/jejune_project/blob/main/Role.md#catalog-curator).

The format of the `catalog.yaml` file is formally defined by the `jejune_cli` utility, but it boils down by a successions of `name`, `url`, `public` fields, each of which pointing to a specific `jejune_doc_*` repository.

## catalog commands

Catalog commands are built into `jejune_cli` directly (no separate install needed):

```sh
jejune catalog check              # validate entries against GitHub + local clones
jejune catalog sync               # find unregistered jj_doc_* repos
jejune catalog check-deployment /path/to/deploy_name
```
