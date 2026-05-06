# Changelog

All notable changes to `scitex-hpc` are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning
follows [SemVer](https://semver.org/).

## [Unreleased]

### Added
- `reservations book --nodelist NODE` — pin a hold-job to a specific
  compute node (useful for landing on a node the operator already
  needs ssh access to via `pam_slurm_adopt`, or one with a specific
  hardware feature).
- `reservations book --account` / `--qos` — surface SLURM account /
  QOS as first-class flags. Routed through the standard
  `JobConfig.resolve()` cascade so `~/.scitex/hpc/config.yaml`
  defaults propagate.
- `mcp start | doctor | install` — convention §3 MCP CLI compliance.
- `install-shell-completion` / `print-shell-completion` — §1a CLI
  surface.

### Changed
- `reservations book --host` is no longer `required=True` at the
  Click layer. The package's `JobConfig.resolve("host")` cascade
  (env `SCITEX_HPC_HOST` → `~/.scitex/hpc/config.yaml`) now fills
  the value when omitted, matching the rest of the SciTeX
  convention. Operators with a single cluster never type `--host`
  again. The Python API still raises a clear `ValueError` when the
  cascade comes up empty.
