# Contributing to scitex-hpc

`scitex-hpc` is the SciTeX ecosystem's generic SLURM dispatch +
persistent-reservation tool. Conventions follow
`~/.claude/skills/scitex/general/`.

## Quick path

```bash
git clone https://github.com/ywatanabe1989/scitex-hpc.git
cd scitex-hpc
pip install -e ".[dev]"
pytest tests/
```

## Branch model

- `main` — release-only; tags cut from here.
- `develop` — default integration branch.
- `feat/<short-name>` / `fix/<short-name>` — feature branches; merge
  into `develop` once tests are green and reviewer-approved.

## Running tests

```bash
pytest tests/                       # full suite
pytest -m integration               # opt-in (requires SSH to a real cluster)
```

## Linting and audits

```bash
scitex-dev ecosystem audit-all scitex-hpc
```

Covers CLI hygiene (mutating verbs need `--dry-run` / `--yes`; help
blocks must include `Example:`), MCP tool naming, project structure,
Python API exports.

## Coding conventions

- **No required CLI flags that have a config / env cascade.** If a
  `JobConfig` field has a `resolve()` chain (env → yaml), the
  matching Click option must default to `None` and let the
  Python layer raise on a fully-empty cascade. The auditor calls
  this E1 (see `~/proj/scitex-dev/GITIGNORED/ESCALATION_FROM_SCITEX_CLEW.md`).
- **Comments**: write *why*, never *what*. No comments on simple lines.
- **State**: lease files at `~/.scitex/hpc/leases/<host>-<name>.json`.

## Filing changes

1. Branch off `develop`.
2. Land tests alongside code.
3. Update `CHANGELOG.md` `[Unreleased]` section.
4. PR into `develop`. CI must be green.

## Release

`develop → main` PR. Tag from `main` after merge. `pyproject.toml`
version bump in the same PR. CI publishes to PyPI on tag push.

## License + CLA

AGPL-3.0-only. PRs require CLA acknowledgement (the bot will prompt
on first contribution).
