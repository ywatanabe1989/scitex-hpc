---
description: |
  [TOPIC] Compatibility Policies
  [DETAILS] HPC compatibility — no-daemon policies, login-shell wrapping,
  state file layout, empirical guarantees from live Spartan verification, and
  source layout.
tags: [scitex-hpc-compatibility-policies, scitex-hpc, scitex-package]
---

# Compatibility & policies

## No daemons

Per the 2026-04-26 IT Security ruling, scitex-hpc never installs
`crontab @reboot`, `systemctl --user enable + linger`, autossh,
cloudflared, or any persistent user-space daemon. All ssh calls are
bastion-initiated; the only persistent thing on Spartan is the SLURM
job itself, which is supposed to be there.

## Login-shell wrapping

Every remote command runs as `bash -lc '<cmd>'` so SLURM's module
system is on PATH.

## State files

`~/.scitex/hpc/leases/<host>-<name>.json` — one JSON per lease.
Override the directory via `SCITEX_HPC_LEASE_DIR` (used by tests).
Format is plain JSON (one dataclass dump per file);
`Reservation.list()` round-trips through it.

## Empirical guarantees (live-verified on Spartan)

- `srun --jobid=<existing> --overlap <cmd>` from a fresh ssh login
  attaches commands to a live job ✅
- `Reservation.book + exec + release` end-to-end in ~41s on
  spartan-bm021 ✅
- Two tenant tmux sessions co-resident in one allocation
  (spartan-bm005) — both survive setup, stop is independent ✅
- Banner-noise tolerant: `_squeue_state` filters DISPLAY/XAUTHORITY
  lines that login shells emit ✅

## Where things live

| File | What |
|---|---|
| `_config.py` | `JobConfig` + default-resolution cascade (env → user yaml → built-ins) |
| `_dispatch.py` | One-shot `srun`/`sbatch` |
| `_reservation.py` | `Reservation` class (Phase 1-4) |
| `_cli.py` | `scitex-hpc reservations …` argparse CLI |
| `_results.py` / `_sync.py` | `poll_job` + `fetch_result` + `sync` (rsync) |

## See also

- `scitex-agent-container` — the `slurm-tenant` runtime consumes `Reservation`
- The 2026-04-26 IT Security ruling in
  `scitex-orochi-private/incident-2026-04-26-spartan-cloudflared-detection.md`
  (private skill)
