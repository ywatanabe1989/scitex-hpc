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

> **Interfaces:** Python ⭐⭐⭐ (primary) · CLI ⭐⭐ · MCP — · Skills ⭐ · Hook — · HTTP —

Generic SLURM dispatch + persistent reservations. Login nodes never run compute — every command is wrapped in `srun`/`sbatch` via a login-shell SSH so the SLURM module loads correctly.

## Two patterns

| Pattern | Use when | Module |
|---|---|---|
| **One-shot dispatch** (`srun`/`sbatch`) | Run a script once, fetch results | `scitex_hpc.{srun,sbatch,sync,poll_job,fetch_result}` |
| **Reservations** (book once, exec many) | Iteration loops; multi-agent fleets; jupyter-on-HPC | `scitex_hpc.Reservation` |

## Reservations — the high-impact API

```python
from scitex_hpc import JobConfig, Reservation

# Once: book a 7-day allocation
res = Reservation.book(
    JobConfig(host="spartan", partition="cascade",
              cpus=8, mem="32G", time="7-0", project="dev-pool"),
    persistent=True,        # walltime auto-resubmit via SIGUSR1 trap
    tmux_server="sac",      # bootstrap a long-lived tmux server (Phase 4)
)

# Many times: run commands inside the allocation — no queue wait
res.exec("hostname")                        # → "spartan-bm022.hpc..."
res.exec(["python", "-m", "pytest", "-n", "8"])
res.attach(cmd="bash")                      # interactive shell on compute node

# Look up later (state lives in ~/.scitex/hpc/leases/)
res = Reservation.get("dev-pool")
res.refresh()                               # picks up new job_id after walltime-resubmit
res.release()                               # scancel + clear state
```

### CLI equivalent

```bash
scitex-hpc reservations book dev-pool --host spartan --cpus 8 --mem 32G \
    --time 7-0 --tmux-server sac --persistent
scitex-hpc reservations list
scitex-hpc reservations exec dev-pool 'hostname'
scitex-hpc reservations attach dev-pool
scitex-hpc reservations release dev-pool
```

## Three reservation features that change the shape of HPC work

### 1. Walltime auto-resubmit (`persistent=True`)

`#SBATCH --signal=B:USR1@3600` makes SLURM signal the script 1h before walltime; a trap calls `sbatch "$0"` to resubmit the script in place. The friendly name (`dev-pool`) stays stable across resubmits; only the SLURM `job_id` changes. Use `Reservation.refresh()` to pick up the new id.

This is SLURM's documented signaling mechanism — not a custom daemon, not a tunnel, not crontab @reboot. Compatible with HPC policies that ban persistent user-space daemons.

### 2. Tmux-server bootstrap (`tmux_server="<socket>"`)

The sbatch script runs `tmux -L <socket> new-session -d -s _root 'sleep infinity'` BEFORE the hold body. The tmux server is then **PID 1 of the sbatch script** (the job's main process), so it lives in the job's cgroup — not a transient `srun --overlap` step's cgroup that would kill it.

Without this bootstrap: `tmux new-session` via `srun --overlap` creates a session that gets cgroup-killed within 2 seconds (verified live on Spartan 2026-04-28). With it: tenants connect via `tmux -L <socket> new-session -t _root` and their sessions outlive their setup commands.

This unblocks scitex-agent-container's `runtime: slurm-tenant` — many agents in one allocation.

### 3. Adopt-existing-jobid (`Reservation.from_jobid`)

Consumers that submit their own sbatch scripts (e.g. sac's `runtime: slurm` with custom hardeners) can still gain the Reservation API surface (`exec`, `attach`, `refresh`, `release`, `list`) by adopting their job:

```python
res = Reservation.from_jobid(host="spartan", job_id="24158160",
                             name="head-spartan", persistent=True)
```

## Empirical guarantees (live-verified on Spartan)

- `srun --jobid=<existing> --overlap <cmd>` from a fresh ssh login attaches commands to a live job ✅
- `Reservation.book + exec + release` end-to-end in ~41s on spartan-bm021 ✅
- Two tenant tmux sessions co-resident in one allocation (spartan-bm005) — both survive setup, stop is independent ✅
- Banner-noise tolerant: `_squeue_state` filters DISPLAY/XAUTHORITY lines that login shells emit ✅

## Compatibility notes

**No daemons on Spartan.** Per the 2026-04-26 IT Security ruling, scitex-hpc never installs `crontab @reboot`, `systemctl --user enable + linger`, autossh, cloudflared, or any persistent user-space daemon. All ssh calls are bastion-initiated; the only persistent thing on Spartan is the SLURM job itself, which is supposed to be there.

**Login-shell wrapping.** Every remote command runs as `bash -lc '<cmd>'` so SLURM's module system is on PATH.

## State files

`~/.scitex/hpc/leases/<host>-<name>.json` — one JSON per lease. Override the directory via `SCITEX_HPC_LEASE_DIR` (used by tests). Format is plain JSON (one dataclass dump per file); `Reservation.list()` round-trips through it.

## Where things live

| File | What |
|---|---|
| `_config.py` | `JobConfig` + default-resolution cascade (env → user yaml → built-ins) |
| `_dispatch.py` | One-shot `srun`/`sbatch` |
| `_reservation.py` | `Reservation` class (Phase 1-4) |
| `_cli.py` | `scitex-hpc reservations …` argparse CLI |
| `_results.py` / `_sync.py` | `poll_job` + `fetch_result` + `sync` (rsync) |

## When to use what

- **Just submit a job and read the result** → `sbatch` + `poll_job` + `fetch_result`
- **Run interactively** → `srun`
- **Iterate against a held allocation** → `Reservation.book(persistent=True).exec(...)` repeatedly
- **Multi-agent fleet on one node** → `Reservation.book(persistent=True, tmux_server="sac")` + scitex-agent-container's `runtime: slurm-tenant`

## See also

- `scitex-agent-container` — the `slurm-tenant` runtime consumes `Reservation`
- The 2026-04-26 IT Security ruling in `scitex-orochi-private/incident-2026-04-26-spartan-cloudflared-detection.md` (private skill)
