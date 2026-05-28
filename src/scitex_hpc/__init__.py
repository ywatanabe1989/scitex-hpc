"""scitex-hpc — generic SLURM dispatch for the SciTeX ecosystem.

Dispatch verbs exported from ``scitex_hpc``.

- ``JobConfig`` is the config struct shared by every dispatch verb.
- ``srun`` runs a blocking interactive job via srun.
- ``sbatch`` submits an async batch job and returns the job ID.
- ``sync`` rsyncs a project tree to the HPC host.
- ``poll_job`` checks sacct status for a job ID.
- ``fetch_result`` scps the full output of a sbatch job.

HPC-awareness helpers (Phase 1 of the HPC-aware Apptainer story).
Pairs with the inside-SIF apptainer bundle in
scitex-agent-container#239 (operator decision 2026-05-28, Telegram
msgs 6705 and 6709).

- ``detect_module_system`` returns ``'lmod'``, ``'tcl'``, or ``None``.
- ``module_load`` runs ``module load X`` and returns the env-var diff.
- ``load_apptainer`` resolves the apptainer binary via Lmod if needed.

No cluster names are baked in.
Defaults resolve via cascade — explicit ``JobConfig(...)``,
``SCITEX_HPC_*`` env vars, ``~/.scitex/{hpc,dev}/config.yaml``
(``hpc.defaults.*``), then cluster-agnostic fallbacks.
Set your ``host`` / ``partition`` once in user config and forget.

Login nodes never run compute — every command is wrapped in srun/sbatch.
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
