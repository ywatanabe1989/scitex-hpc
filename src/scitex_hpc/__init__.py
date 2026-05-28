"""scitex-hpc — generic SLURM dispatch for the SciTeX ecosystem.

Public API:

    from scitex_hpc import (
        JobConfig,
        srun,                  # blocking interactive run via srun
        sbatch,                # async batch submission, returns job ID
        sync,                  # rsync project to HPC host
        poll_job,              # check sacct status for a job ID
        fetch_result,          # scp the full output of a sbatch job
        detect_module_system,  # 'lmod' | 'tcl' | None for the current host
        module_load,           # `module load X` -> env-var diff dict
        load_apptainer,        # resolve apptainer binary via Lmod if needed
    )

No cluster names are baked in. Defaults resolve via cascade:
explicit `JobConfig(...)` → `SCITEX_HPC_*` env vars →
`~/.scitex/{hpc,dev}/config.yaml` (`hpc.defaults.*`) → cluster-agnostic
fallbacks. Set your `host` / `partition` once in user config and forget.

Login nodes never run compute — every command is wrapped in srun/sbatch.

HPC-awareness helpers (`detect_module_system`, `module_load`,
`load_apptainer`) — see `_modules.py` docstring. Phase 1 of the
HPC-aware Apptainer story; pairs with the inside-SIF apptainer bundle
in scitex-agent-container#239 (operator decision 2026-05-28, Telegram
msgs 6705 / 6709).
"""

from __future__ import annotations

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _v

    try:
        __version__ = _v("scitex-hpc")
    except PackageNotFoundError:
        __version__ = "0.0.0+local"
    del _v, PackageNotFoundError
except ImportError:  # pragma: no cover — only on ancient Pythons
    __version__ = "0.0.0+local"
from ._config import HPC_DEFAULTS, JobConfig
from ._dispatch import sbatch, srun
from ._modules import detect_module_system, load_apptainer, module_load
from ._reservation import Reservation
from ._results import fetch_result, poll_job
from ._sync import sync

__all__ = [
    "__version__",
    "HPC_DEFAULTS",
    "JobConfig",
    "Reservation",
    "detect_module_system",
    "fetch_result",
    "load_apptainer",
    "module_load",
    "poll_job",
    "sbatch",
    "srun",
    "sync",
]
