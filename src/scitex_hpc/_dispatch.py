"""srun (blocking) and sbatch (async) dispatch helpers."""

from __future__ import annotations

import re

from scitex_ssh import exec_remote

from ._config import JobConfig


def _wrap_in_login_shell(remote_cmd: str) -> str:
    """Wrap a remote command so SLURM tools resolve via the login shell.

    Spartan (and many HPCs) expose ``srun`` / ``sbatch`` only after the
    module system loads, which happens in a login shell. ``bash -lc`` makes
    the wrapper portable across hosts that put SLURM in different paths.
    """
    return f"bash -lc {_quote(remote_cmd)}"


def _quote(s: str) -> str:
    """POSIX-shell-quote a string."""
    return "'" + s.replace("'", "'\\''") + "'"


def srun(config: JobConfig) -> int:
    """Run ``config.command`` synchronously inside an srun allocation.

    Returns the remote pytest / shell exit code.
    """
    if not config.command:
        raise ValueError("JobConfig.command is required for srun()")

    host = config.resolve("host")
    remote_base = config.resolve("remote_base")

    inner = (
        f"cd {remote_base}/{config.project} && "
        f"srun {' '.join(config.slurm_args())} "
        f"{' '.join(config.extra_srun_args)} "
        f"bash -lc {_quote(config.command)}"
    )

    result = exec_remote(host, _wrap_in_login_shell(inner))
    return result.returncode


def sbatch(config: JobConfig) -> str | None:
    """Submit a batch job; return the SLURM job ID (e.g. ``"24386489"``).

    Returns None on submission failure.
    """
    if not config.command:
        raise ValueError("JobConfig.command is required for sbatch()")

    host = config.resolve("host")
    remote_base = config.resolve("remote_base")
    output_dir = ".pytest-hpc-output"

    sbatch_args = [
        *config.slurm_args(),
        f"--output={output_dir}/%x-%j.out",
        *config.extra_sbatch_args,
    ]

    # Use a heredoc-style wrapper so the user's command can contain
    # arbitrary shell. Submission via stdin via `sbatch <(echo ...)` would
    # be cleaner but isn't portable across all login shells.
    script_body = (
        "#!/bin/bash\n"
        "#SBATCH " + "\n#SBATCH ".join(sbatch_args) + "\n"
        f"cd {remote_base}/{config.project}\n"
        f"{config.command}\n"
    )
    inner = (
        f"mkdir -p {remote_base}/{config.project}/{output_dir} && "
        f"cd {remote_base}/{config.project} && "
        f"sbatch <(printf %s {_quote(script_body)})"
    )

    result = exec_remote(host, _wrap_in_login_shell(inner))
    if result.returncode != 0:
        return None
    # ``sbatch`` prints "Submitted batch job <ID>" on success.
    m = re.search(r"Submitted batch job (\d+)", result.stdout)
    return m.group(1) if m else None
