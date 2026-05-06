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

from scitex_config._ecosystem import local_state

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
    # Optional scheduling pins. Empty string = "let SLURM pick" / "no
    # pin". Listed here so the user-config cascade picks them up from
    # ~/.scitex/hpc/config.yaml the same way as host/partition.
    "nodelist": "",
    "account": "",
    "qos": "",
}


def _user_config_candidates() -> tuple[Path, ...]:
    """Candidate user-config paths, resolved at call time so $SCITEX_DIR
    relocates them per the local-state-directories spec §6."""
    return (
        local_state.path("hpc", "config.yaml"),
        local_state.path("dev", "config.yaml"),
    )


_KEY_ALIASES = {"cpus_per_task": "cpus"}  # tolerate legacy SLURM-style key


def _load_user_defaults() -> dict[str, Any]:
    """Merge defaults from user config files.

    Recognised layouts (first non-empty wins per key):

    - ``~/.scitex/hpc/config.yaml`` flat top-level (``host: ...``, ``partition: ...``)
    - ``~/.scitex/dev/config.yaml`` nested as ``hpc.defaults.<key>``

    ``cpus_per_task`` is accepted as an alias for ``cpus``.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    merged: dict[str, Any] = {}
    valid_keys = set(HPC_DEFAULTS) | set(_KEY_ALIASES)
    for path in _user_config_candidates():
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        candidates: list[dict] = []
        nested = (data.get("hpc") or {}).get("defaults") or {}
        if isinstance(nested, dict):
            candidates.append(nested)
        flat = {k: v for k, v in data.items() if k in valid_keys}
        if flat:
            candidates.append(flat)
        for src in candidates:
            for k, v in src.items():
                key = _KEY_ALIASES.get(k, k)
                merged.setdefault(key, v)
    return merged


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

    # SLURM scheduling pins. All three are optional; the cluster's
    # defaults apply when unset.
    #
    # ``nodelist``    pins the allocation to a specific node — e.g.
    #                 ``spartan-bm198``. Useful when the operator needs
    #                 to land work on a node they can already ssh into,
    #                 or one with a specific hardware feature (GPU, big
    #                 RAM). When ``nodelist`` is set the scheduler will
    #                 wait until that node has free resources rather
    #                 than picking another.
    # ``account``     SLURM account / project to bill (Spartan: punim2354).
    # ``qos``         quality-of-service tier (Spartan: publiccpu, etc.).
    nodelist: str | None = None
    account: str | None = None
    qos: str | None = None

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
        cluster's site-default partition applies. ``nodelist`` /
        ``account`` / ``qos`` are emitted only when set (cluster defaults
        otherwise apply)."""
        args = [
            f"--cpus-per-task={self.resolve('cpus')}",
            f"--time={self.resolve('time')}",
            f"--mem={self.resolve('mem')}",
        ]
        partition = self.resolve("partition")
        if partition:
            args.insert(0, f"--partition={partition}")
        # Optional scheduling pins.
        for key in ("nodelist", "account", "qos"):
            val = getattr(self, key, None)
            if val:
                # SLURM accepts either form; the long form is most
                # readable in scontrol output.
                args.append(f"--{key}={val}")
        name = self.job_name or f"scitex-{self.project}"
        args.append(f"--job-name={name}")
        return args
