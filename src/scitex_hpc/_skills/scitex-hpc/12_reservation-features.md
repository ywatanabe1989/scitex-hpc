---
description: |
  [TOPIC] Reservation Features
  [DETAILS] Three reservation features that change the shape of HPC work — walltime auto-resubmit (SIGUSR1 trap), tmux-server bootstrap (PID 1 of sbatch), adopt-existing-jobid for consumers with custom sbatch hardeners.
tags: [scitex-hpc-reservation-features, scitex-hpc, scitex-package]
---

# Three reservation features that change the shape of HPC work

## 1. Walltime auto-resubmit (`persistent=True`)

`#SBATCH --signal=B:USR1@3600` makes SLURM signal the script 1h before
walltime; a trap calls `sbatch "$0"` to resubmit the script in place. The
friendly name (`dev-pool`) stays stable across resubmits; only the SLURM
`job_id` changes. Use `Reservation.refresh()` to pick up the new id.

This is SLURM's documented signaling mechanism — not a custom daemon, not
a tunnel, not crontab @reboot. Compatible with HPC policies that ban
persistent user-space daemons.

## 2. Tmux-server bootstrap (`tmux_server="<socket>"`)

The sbatch script runs `tmux -L <socket> new-session -d -s _root 'sleep
infinity'` BEFORE the hold body. The tmux server is then **PID 1 of the
sbatch script** (the job's main process), so it lives in the job's
cgroup — not a transient `srun --overlap` step's cgroup that would kill
it.

Without this bootstrap: `tmux new-session` via `srun --overlap` creates
a session that gets cgroup-killed within 2 seconds (verified live on
Spartan 2026-04-28). With it: tenants connect via `tmux -L <socket>
new-session -t _root` and their sessions outlive their setup commands.

This unblocks scitex-agent-container's `runtime: slurm-tenant` — many
agents in one allocation.

## 3. Adopt-existing-jobid (`Reservation.from_jobid`)

Consumers that submit their own sbatch scripts (e.g. sac's `runtime: slurm`
with custom hardeners) can still gain the Reservation API surface (`exec`,
`attach`, `refresh`, `release`, `list`) by adopting their job:

```python
res = Reservation.from_jobid(host="spartan", job_id="24158160",
                             name="head-spartan", persistent=True)
```
