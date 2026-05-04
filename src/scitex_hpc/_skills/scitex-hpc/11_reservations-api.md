---
description: |
  [TOPIC] Reservations API
  [DETAILS] Reservation primitive — book a SLURM allocation once, run many short commands inside via srun --jobid --overlap. Cuts queue wait from minutes to one ssh round-trip per command.
tags: [scitex-hpc-reservations-api, scitex-hpc, scitex-package]
---

# Reservations API

`Reservation` lets you book a node once, then exec many short commands
inside the live allocation via `srun --jobid --overlap` — cutting queue
wait from minutes to one ssh round-trip per command.

## Python

```python
from scitex_hpc import JobConfig, Reservation

# Once: book a 7-day allocation
res = Reservation.book(
    JobConfig(host="spartan", partition="cascade",
              cpus=8, mem="32G", time="7-0", project="dev-pool"),
    persistent=True,        # walltime auto-resubmit via SIGUSR1 trap
    tmux_server="sac",      # bootstrap a long-lived tmux server
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

## CLI equivalent

```bash
scitex-hpc reservations book dev-pool --host spartan --cpus 8 --mem 32G \
    --time 7-0 --tmux-server sac --persistent
scitex-hpc reservations list
scitex-hpc reservations exec dev-pool 'hostname'
scitex-hpc reservations attach dev-pool
scitex-hpc reservations release dev-pool
```

## When to use what

- **Just submit a job and read the result** → `sbatch` + `poll_job` + `fetch_result`
- **Run interactively** → `srun`
- **Iterate against a held allocation** → `Reservation.book(persistent=True).exec(...)` repeatedly
- **Multi-agent fleet on one node** → `Reservation.book(persistent=True, tmux_server="sac")` + scitex-agent-container's `runtime: slurm-tenant`
