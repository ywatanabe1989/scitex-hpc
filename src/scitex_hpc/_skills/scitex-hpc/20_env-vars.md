---
description: |
  [TOPIC] Environment Variables
  [DETAILS] `SCITEX_HPC_*` overrides recognised by `JobConfig.resolve()`. Every JobConfig field participates in the cascade `direct → SCITEX_HPC_<UPPER> → ~/.scitex/{hpc,dev}/config.yaml → built-in default`. Drop your cluster's host/partition/account into the env or yaml once and forget. Read this when scripting against scitex-hpc from an environment where flags are inconvenient (CI, agent runners).
tags: [scitex-hpc-env-vars, scitex-hpc, scitex-package]
---

# scitex-hpc environment variables

`JobConfig.resolve(<key>)` walks four sources for every field:

1. Direct value passed to `JobConfig(...)` or the matching `--<flag>` on the CLI
2. `SCITEX_HPC_<UPPER>` environment variable
3. `~/.scitex/hpc/config.yaml` (flat) or `~/.scitex/dev/config.yaml` (nested under `hpc.defaults.<key>`)
4. Built-in cluster-agnostic fallback (see `HPC_DEFAULTS` in `_config.py`)

## Recognised variables

| Variable | Built-in default | Maps to JobConfig field / CLI flag |
|---|---|---|
| `SCITEX_HPC_HOST` | `""` | `host` / `--host` |
| `SCITEX_HPC_PARTITION` | `""` | `partition` / `--partition` |
| `SCITEX_HPC_CPUS` | `4` | `cpus` / `--cpus` |
| `SCITEX_HPC_TIME` | `00:20:00` | `time` / `--time` |
| `SCITEX_HPC_MEM` | `8G` | `mem` / `--mem` |
| `SCITEX_HPC_REMOTE_BASE` | `~/proj` | `remote_base` |
| `SCITEX_HPC_PYTHON_BIN` | `python3` | `python_bin` |
| `SCITEX_HPC_NODELIST` | `""` | `nodelist` / `--nodelist` |
| `SCITEX_HPC_ACCOUNT` | `""` | `account` / `--account` |
| `SCITEX_HPC_QOS` | `""` | `qos` / `--qos` |
| `SCITEX_HPC_GPUS` | `""` | `gpus` / `--gpus` (pass-through to SLURM `--gpus=<SPEC>`) |

Other knobs not in the field cascade:

| Variable | Default | Purpose |
|---|---|---|
| `SCITEX_HPC_LEASE_DIR` | `~/.scitex/hpc/runtime/leases` | Where reservation lease JSON files live (used in tests via `monkeypatch.setenv`) |
| `SCITEX_HPC_CONFIG` | unset | Explicit user-config path (overrides the `~/.scitex/...` lookup) |

## Example: forget the per-cluster knobs

`~/.scitex/hpc/config.yaml`:

```yaml
host: spartan
partition: sapphire
cpus: 32
mem: 128G
time: 7-0
account: punim2354
qos: publiccpu
```

After this, every `scitex_hpc.{srun,sbatch}` and `scitex-hpc reservations
book` call inherits these — you only need to pass `--gpus` (or anything
else workload-specific) on the command line.

See `general/01_ecosystem_04_environment-variables.md` and
`general/01_ecosystem_06_local-state-directories.md` for the
cross-package conventions on env-var naming and state-dir layout.
