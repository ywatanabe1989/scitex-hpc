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

## Reservations — the high-impact API

```python
from scitex_hpc import JobConfig, Reservation

# Once: book a 7-day allocation
res = Reservation.book(
    JobConfig(host="spartan", partition="cascade",
              cpus=8, mem="32G", time="7-0", project="dev-pool"),
    persistent=True,        # walltime auto-resubmit via SIGUSR1 trap
    tmux_server="sac",      # bootstrap a long-lived tmux server
)

# Many times — no queue wait
res.exec("hostname")
res.exec(["python", "-m", "pytest", "-n", "8"])
res.attach(cmd="bash")

res = Reservation.get("dev-pool")    # state in ~/.scitex/hpc/leases/
res.refresh()                        # picks up new job_id after walltime-resubmit
res.release()
```

CLI mirror: `scitex-hpc reservations {book,list,exec,attach,release}`.

## Reservation features (one-line summary)

- **Walltime auto-resubmit** — `#SBATCH --signal=B:USR1@3600` traps and
  re-`sbatch`'s in place; friendly name stable, only `job_id` changes.
- **Tmux-server bootstrap** — sbatch script's PID 1 is `tmux -L <socket>`;
  tenants attach via `tmux -L <socket> new-session -t _root` and survive
  cgroup-kill that would otherwise hit `srun --overlap` sessions.
- **Adopt-existing-jobid** — `Reservation.from_jobid(...)` lets consumers
  who submit their own sbatch scripts gain the API surface without rebooking.

## Compatibility

- **No daemons** (per 2026-04-26 IT Security ruling) — never `crontab @reboot`,
  `systemctl --user linger`, autossh, cloudflared. Bastion-initiated SSH only.
- **Login-shell wrapping** — every remote command runs as `bash -lc '<cmd>'`
  so SLURM's module system is on PATH.
- State at `~/.scitex/hpc/leases/<host>-<name>.json`. Override via
  `SCITEX_HPC_LEASE_DIR`.

## See also

- `scitex-agent-container` — `slurm-tenant` runtime consumes `Reservation`
