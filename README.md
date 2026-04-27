# scitex-hpc

<!-- scitex-badges:start -->
[![PyPI](https://img.shields.io/pypi/v/scitex-hpc.svg)](https://pypi.org/project/scitex-hpc/)
[![Python](https://img.shields.io/pypi/pyversions/scitex-hpc.svg)](https://pypi.org/project/scitex-hpc/)
[![Tests](https://github.com/ywatanabe1989/scitex-hpc/actions/workflows/test.yml/badge.svg)](https://github.com/ywatanabe1989/scitex-hpc/actions/workflows/test.yml)
[![Install Test](https://github.com/ywatanabe1989/scitex-hpc/actions/workflows/install-test.yml/badge.svg)](https://github.com/ywatanabe1989/scitex-hpc/actions/workflows/install-test.yml)
[![Coverage](https://codecov.io/gh/ywatanabe1989/scitex-hpc/graph/badge.svg)](https://codecov.io/gh/ywatanabe1989/scitex-hpc)
[![Docs](https://readthedocs.org/projects/scitex-hpc/badge/?version=latest)](https://scitex-hpc.readthedocs.io/en/latest/)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
<!-- scitex-badges:end -->

Generic SLURM dispatch for the [SciTeX](https://github.com/ywatanabe1989/scitex-python) ecosystem — `srun` / `sbatch` / `sync` / `poll_job` / `fetch_result` with sane defaults for spartan/sapphire and override knobs for any other cluster.

**Login nodes never run compute** — every command is wrapped in `srun` or `sbatch` via a login-shell SSH so the SLURM module loads correctly.

## Install

```bash
pip install scitex-hpc
```

## Usage

```python
from scitex_hpc import JobConfig, srun, sbatch, sync, poll_job, fetch_result

cfg = JobConfig(
    project="scitex-dsp",
    command="pip install -e '.[dev]' -q && python -m pytest tests/ -n 16",
    host="spartan",
    partition="sapphire",
    cpus=16,
    time="00:30:00",
    mem="64G",
)

# 1. Sync local sources to the cluster.
sync(cfg)

# 2a. Blocking interactive run via srun.
exit_code = srun(cfg)

# 2b. Async batch submission via sbatch.
job_id = sbatch(cfg)
print(poll_job(cfg, job_id))   # {'state': 'COMPLETED', 'exit_code': '0:0', 'elapsed': '00:01:23'}
fetch_result(cfg, job_id)      # downloads the .out file
```

## Reservations (book once, exec many)

For workflows where queue wait dominates iteration time — multi-agent
fleets, distributed test runners, jupyter-on-HPC — book a node *once*
and run many short commands inside its allocation:

```python
from scitex_hpc import JobConfig, Reservation

# Book a 7-day allocation
res = Reservation.book(
    JobConfig(
        project="dev-pool",
        host="spartan",
        partition="cascade",
        cpus=8, mem="32G", time="7-0",
    ),
    persistent=True,        # walltime auto-resubmit (Phase 2)
)

# Run many commands inside the SAME allocation — no queue wait
res.exec("hostname")                          # → "spartan-bm022.hpc..."
res.exec(["python", "-m", "unittest", "discover"])
res.exec("tmux new -d -s helper claude --dangerously-skip-permissions")

# Open an interactive shell on the compute node
res.attach(cmd="bash")

# Or look up later by friendly name (state lives in ~/.scitex/hpc/leases/)
res = Reservation.get("dev-pool")
res.release()                                 # scancel + clear state
```

Equivalent CLI:

```bash
scitex-hpc reservations book dev-pool --host spartan --cpus 8 --mem 32G --time 7-0 --persistent
scitex-hpc reservations list
scitex-hpc reservations exec dev-pool 'hostname'
scitex-hpc reservations attach dev-pool
scitex-hpc reservations release dev-pool
```

**Compatible with bastion-only HPC policies.** No daemons, no tunnels,
no `crontab @reboot`. Every `exec()` is a fresh ssh round-trip. SSH
ControlMaster pooling on the calling host amortizes the handshake cost.

## Defaults & overrides

Every `JobConfig` field has a `SCITEX_HPC_*` env-var override:

| Field | Default | Env override |
|---|---|---|
| `host` | `spartan` | `SCITEX_HPC_HOST` |
| `partition` | `sapphire` | `SCITEX_HPC_PARTITION` |
| `cpus` | `16` | `SCITEX_HPC_CPUS` |
| `time` | `00:20:00` | `SCITEX_HPC_TIME` |
| `mem` | `128G` | `SCITEX_HPC_MEM` |
| `remote_base` | `~/proj` | `SCITEX_HPC_REMOTE_BASE` |

Resolution priority: explicit `JobConfig` field → env var → built-in default.

## Status

Standalone module from the SciTeX ecosystem. Public API surfaces in
`scitex.hpc` (via the umbrella package's `sys.modules` alias) so you can
write `from scitex.hpc import srun` from any consumer.

## License

AGPL-3.0-only.
