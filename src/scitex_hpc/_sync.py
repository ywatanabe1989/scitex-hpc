"""rsync project to HPC host."""

from __future__ import annotations

import os
import subprocess

from ._config import JobConfig

_RSYNC_EXCLUDES = [
    ".git",
    "__pycache__",
    "*.pyc",
    ".eggs",
    "*.egg-info",
    "dist",
    "build",
    "docs/sphinx/_build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "*_out",
    "GITIGNORED",
    ".pytest-hpc-output",
]


def sync(
    config: JobConfig,
    *,
    local_path: str | None = None,
    delete: bool = False,
    dry_run: bool = False,
) -> bool:
    """rsync ``local_path`` (default: cwd) to ``host:remote_base/project/``.

    Parameters
    ----------
    config : JobConfig
        Provides ``host``, ``project``, ``remote_base``.
    local_path : str | None
        Source directory. If None, uses the current working directory.
    delete : bool
        Pass ``--delete`` to rsync so files removed locally vanish remotely.
    dry_run : bool
        Pass ``--dry-run`` so rsync just reports what it would copy.

    Returns
    -------
    bool
        True if rsync exited 0.
    """
    src = (local_path or os.getcwd()).rstrip("/") + "/"
    host = config.resolve("host")
    remote_base = config.resolve("remote_base")
    dest = f"{host}:{remote_base}/{config.project}/"

    cmd = ["rsync", "-az"]
    if delete:
        cmd.append("--delete")
    if dry_run:
        cmd.append("--dry-run")
    for ex in _RSYNC_EXCLUDES:
        cmd.extend(["--exclude", ex])
    cmd.extend([src, dest])

    return subprocess.run(cmd).returncode == 0
