---
name: scitex-hpc
description: Generic SLURM dispatch + persistent reservations for the SciTeX ecosystem. One-shot `srun`/`sbatch`/`sync`/`poll`/`fetch_result` plus a `Reservation` primitive that lets you book a node once and run many short commands inside the allocation via `srun --jobid --overlap` — cuts queue wait from minutes to one ssh round-trip per command. Reservations support walltime auto-resubmit (SIGUSR1 trap) and tmux-server bootstrap (PID 1 of the sbatch script) so multi-tenant agent runtimes can attach long-lived sessions without cgroup-kill. Bastion-initiated SSH only — compatible with HPC policies that ban persistent daemons or outbound tunnels. Drop-in replacement for hand-rolled `ssh hpc 'sbatch ...'` scripts, per-experiment queue-wait penalties, and bespoke `sleep + scancel` watchdogs.
primary_interface: python
interfaces:
  python: 3
  cli: 2
  mcp: 0
  skills: 1
  hook: 0
  http: 0
tags: [scitex-hpc, scitex-package]
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

- [01_reservations-api.md](01_reservations-api.md) — full `Reservation` API + CLI
- [02_reservation-features.md](02_reservation-features.md) — walltime auto-resubmit, tmux bootstrap, adopt-existing-jobid
- [03_compatibility-policies.md](03_compatibility-policies.md) — no-daemon policy, login-shell wrapping, state files, empirical guarantees, source layout
