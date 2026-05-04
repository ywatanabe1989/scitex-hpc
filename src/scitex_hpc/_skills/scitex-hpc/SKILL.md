---
name: scitex-hpc
description: |
  [WHAT] Generic SLURM dispatch + persistent reservations for the SciTeX
  ecosystem — one-shot `srun`/`sbatch`/`sync`/`poll`/`fetch_result` plus a
  `Reservation` primitive that books a node once and runs many short commands
  inside the allocation via `srun --jobid --overlap`, cutting queue wait from
  minutes to one ssh round-trip per command.
  [WHEN] Dispatching jobs to an HPC cluster from a laptop or login node —
  especially when iterating in tight loops, running multi-agent fleets, or
  doing jupyter-on-HPC where queue wait dominates wall time.
  [HOW] `from scitex_hpc import srun, sbatch, Reservation`, or
  `scitex-hpc <verb> ...`. Bastion-initiated SSH only; no persistent daemons.
primary_interface: python
interfaces:
  python: 3
  cli: 2
  mcp: 0
  skills: 1
  hook: 0
  http: 0
tags: [scitex-hpc]
---

# scitex-hpc

Generic SLURM dispatch + persistent reservations. Login nodes never run
compute — every command is wrapped in `srun`/`sbatch` via a login-shell SSH.

## Two patterns

| Pattern | Use when | Module |
|---|---|---|
| **One-shot dispatch** (`srun`/`sbatch`) | Run a script once, fetch results | `scitex_hpc.{srun,sbatch,sync,poll_job,fetch_result}` |
| **Reservations** (book once, exec many) | Iteration loops; multi-agent fleets; jupyter-on-HPC | `scitex_hpc.Reservation` |

## Sub-skills

### Core (01–09)
- [01_installation.md](01_installation.md) — install + import sanity check
- [02_quick-start.md](02_quick-start.md) — 30-second tour
- [03_python-api.md](03_python-api.md) — Python API surface
- [04_cli-reference.md](04_cli-reference.md) — CLI subcommands

### Workflows (10–19)
- [11_reservations-api.md](11_reservations-api.md) — full `Reservation` API + CLI
- [12_reservation-features.md](12_reservation-features.md) — walltime auto-resubmit, tmux bootstrap, adopt-existing-jobid
- [13_compatibility-policies.md](13_compatibility-policies.md) — no-daemon policy, login-shell wrapping, state files, empirical guarantees, source layout

### Meta (20+)
- [20_env-vars.md](20_env-vars.md) — Environment variables (`SCITEX_HPC_*`)
