"""Poll job status + fetch result files."""

from __future__ import annotations

import subprocess

from ._config import JobConfig


def poll_job(config: JobConfig, job_id: str) -> dict:
    """Return ``sacct`` status for ``job_id``.

    Result keys: ``state`` (e.g. ``RUNNING`` / ``COMPLETED`` / ``FAILED``),
    ``exit_code`` (None when not yet finished), ``elapsed`` (HH:MM:SS).
    Empty dict if sacct doesn't know the job (queued for too long, or
    invalid ID).
    """
    host = config.resolve("host")
    cmd = f"sacct -j {job_id} -X --format=State,ExitCode,Elapsed --parsable2 -n"
    result = subprocess.run(
        ["ssh", host, f"bash -lc '{cmd}'"],
        capture_output=True,
        text=True,
    )
    line = (result.stdout or "").strip().splitlines()
    if not line:
        return {}
    parts = line[0].split("|")
    if len(parts) < 3:
        return {}
    state, exit_code, elapsed = parts[:3]
    return {
        "state": state.strip(),
        "exit_code": exit_code.strip() if exit_code.strip() else None,
        "elapsed": elapsed.strip(),
    }


def fetch_result(
    config: JobConfig,
    job_id: str,
    *,
    local_dir: str = ".pytest-hpc-output",
) -> bool:
    """scp the sbatch job's stdout file back to ``local_dir``.

    The remote file is at
    ``{remote_base}/{project}/.pytest-hpc-output/<job_name>-<job_id>.out``.

    Returns True if scp exited 0.
    """
    host = config.resolve("host")
    remote_base = config.resolve("remote_base")
    name = config.job_name or f"scitex-{config.project}"
    remote_path = (
        f"{host}:{remote_base}/{config.project}/.pytest-hpc-output/{name}-{job_id}.out"
    )

    import os

    os.makedirs(local_dir, exist_ok=True)
    return subprocess.run(["scp", remote_path, local_dir]).returncode == 0
