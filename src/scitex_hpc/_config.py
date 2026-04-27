"""JobConfig + default-resolution helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Defaults match typical SciTeX use: Spartan / sapphire / 16-core / 20-min.
HPC_DEFAULTS = {
    "host": "spartan",
    "partition": "sapphire",
    "cpus": 16,
    "time": "00:20:00",
    "mem": "128G",
    "remote_base": "~/proj",
    "python_bin": "python3",
}


@dataclass
class JobConfig:
    """Configuration for an HPC dispatch.

    Every field has an environment-variable override:
    ``SCITEX_HPC_HOST``, ``SCITEX_HPC_CPUS``, ``SCITEX_HPC_PARTITION``,
    ``SCITEX_HPC_TIME``, ``SCITEX_HPC_MEM``, ``SCITEX_HPC_REMOTE_BASE``.

    The ``project`` field is required: it identifies the directory under
    ``remote_base/`` where the rsync'd source lives and where pytest runs.
    """

    project: str
    command: str = ""
    """Shell command to execute inside ``remote_base/<project>/``. If
    empty, callers are expected to pass an explicit command to ``srun``
    or ``sbatch`` directly."""

    host: str | None = None
    partition: str | None = None
    cpus: int | None = None
    time: str | None = None
    mem: str | None = None
    remote_base: str | None = None
    python_bin: str | None = None

    extra_sbatch_args: list[str] = field(default_factory=list)
    extra_srun_args: list[str] = field(default_factory=list)
    job_name: str | None = None

    def resolve(self, key: str) -> str:
        """Resolve a single field via direct → env → default cascade."""
        direct = getattr(self, key, None)
        if direct is not None:
            if isinstance(direct, int):
                return str(direct)
            return direct
        env_val = os.environ.get(f"SCITEX_HPC_{key.upper()}")
        if env_val is not None:
            return env_val
        default = HPC_DEFAULTS[key]
        return str(default) if isinstance(default, int) else default

    def slurm_args(self) -> list[str]:
        """Return the standard Slurm ``--partition`` / ``--cpus-per-task``
        / ``--time`` / ``--mem`` / ``--job-name`` flags."""
        args = [
            f"--partition={self.resolve('partition')}",
            f"--cpus-per-task={self.resolve('cpus')}",
            f"--time={self.resolve('time')}",
            f"--mem={self.resolve('mem')}",
        ]
        name = self.job_name or f"scitex-{self.project}"
        args.append(f"--job-name={name}")
        return args
