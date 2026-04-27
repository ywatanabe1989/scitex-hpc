"""JobConfig + default-resolution helpers.

Default resolution cascade for any field:

    1. Explicit value passed to ``JobConfig(...)``
    2. ``SCITEX_HPC_<KEY>`` environment variable
    3. ``~/.scitex/dev/config.yaml`` -> ``hpc.defaults.<key>`` (user-level)
    4. Built-in fallback (cluster-agnostic — empty host/partition; modest
       cpus/time/mem; ``~/proj`` remote_base)

No site-specific cluster names are baked into this package. Drop your
preferred ``host`` / ``partition`` (e.g. ``spartan`` / ``sapphire``,
``cedar`` / ``cpubase_bycore_b1``, etc.) into the user config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Cluster-agnostic fallbacks. host/partition empty by design — supply
# them via user config, env var, or explicit JobConfig.
HPC_DEFAULTS: dict[str, Any] = {
    "host": "",
    "partition": "",
    "cpus": 4,
    "time": "00:20:00",
    "mem": "8G",
    "remote_base": "~/proj",
    "python_bin": "python3",
}

_USER_CONFIG_CANDIDATES = (
    Path.home() / ".scitex" / "hpc" / "config.yaml",
    Path.home() / ".scitex" / "dev" / "config.yaml",
)


def _load_user_defaults() -> dict[str, Any]:
    """Read ``hpc.defaults.*`` from the first existing user config file.

    Returns an empty dict if no config is found or yaml is unavailable.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    for path in _USER_CONFIG_CANDIDATES:
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception:
            continue
        hpc = data.get("hpc") or {}
        defaults = hpc.get("defaults") or {}
        if isinstance(defaults, dict):
            return defaults
    return {}


@dataclass
class JobConfig:
    """Configuration for an HPC dispatch.

    Resolution cascade (per field): direct value → ``SCITEX_HPC_<KEY>`` env
    var → ``~/.scitex/{hpc,dev}/config.yaml`` → built-in cluster-agnostic
    default.

    The ``project`` field is required: it identifies the directory under
    ``remote_base/`` where the rsync'd source lives and where ``command``
    runs.
    """

    project: str
    command: str = ""

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
        """Resolve a single field via direct → env → user-config → default."""
        direct = getattr(self, key, None)
        if direct is not None and direct != "":
            return str(direct) if isinstance(direct, int) else direct
        env_val = os.environ.get(f"SCITEX_HPC_{key.upper()}")
        if env_val:
            return env_val
        user = _load_user_defaults().get(key)
        if user not in (None, ""):
            return str(user) if isinstance(user, int) else user
        default = HPC_DEFAULTS[key]
        return str(default) if isinstance(default, int) else default

    def slurm_args(self) -> list[str]:
        """Return standard Slurm flags. Empty partition is omitted so the
        cluster's site-default partition applies."""
        args = [
            f"--cpus-per-task={self.resolve('cpus')}",
            f"--time={self.resolve('time')}",
            f"--mem={self.resolve('mem')}",
        ]
        partition = self.resolve("partition")
        if partition:
            args.insert(0, f"--partition={partition}")
        name = self.job_name or f"scitex-{self.project}"
        args.append(f"--job-name={name}")
        return args
