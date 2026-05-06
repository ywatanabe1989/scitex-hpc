"""scitex-hpc MCP server — single FastMCP instance per package (§1).

Tools registered here use bare names (``skills_list``, ``run_test``)
without the ``hpc_`` prefix; the umbrella bridge in
``scitex/_mcp_tools/hpc.py`` calls ``safe_mount(mcp, sub_mcp,
namespace="hpc")`` to add the prefix when re-exported under ``scitex``.
"""

from __future__ import annotations

from pathlib import Path

try:
    from fastmcp import FastMCP
except ImportError as e:  # pragma: no cover — fastmcp is optional
    raise ImportError(
        "fastmcp is required for scitex-hpc MCP support.\n"
        "Install with: pip install scitex-hpc[mcp]"
    ) from e

mcp = FastMCP("scitex-hpc")


def _skills_root() -> Path:
    import scitex_hpc

    return Path(scitex_hpc.__file__).parent / "_skills" / "scitex-hpc"


def _list_skill_files() -> list[Path]:
    root = _skills_root()
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.md") if p.is_file() and p.name != "SKILL.md")


@mcp.tool()
def skills_list() -> list[dict]:
    """List skill files bundled with scitex-hpc."""
    return [{"name": p.stem, "path": str(p)} for p in _list_skill_files()]


@mcp.tool()
def skills_get(name: str) -> dict:
    """Return the contents of a bundled skill file by NAME (e.g. ``01_installation``)."""
    target = name[:-3] if name.endswith(".md") else name
    match = next((p for p in _list_skill_files() if p.stem == target), None)
    if match is None:
        return {"error": f"skill not found: {name}"}
    return {
        "name": match.stem,
        "path": str(match),
        "content": match.read_text(encoding="utf-8"),
    }


# --------------------------------------------------------------- dispatch tools
# Thin wrappers around the public Python API (sbatch, srun, sync,
# poll_job, fetch_result) so MCP-aware agents can dispatch SLURM work
# with the same semantics as `from scitex_hpc import …`. Each builds a
# JobConfig (so the standard direct → SCITEX_HPC_* → user-yaml → default
# cascade still applies) and delegates straight to the underlying
# function.


def _make_config(
    project: str,
    command: str = "",
    host: str | None = None,
    partition: str | None = None,
    cpus: int | None = None,
    time: str | None = None,
    mem: str | None = None,
    nodelist: str | None = None,
    account: str | None = None,
    qos: str | None = None,
    gpus: str | None = None,
):
    from .._config import JobConfig

    return JobConfig(
        project=project,
        command=command,
        host=host,
        partition=partition,
        cpus=cpus,
        time=time,
        mem=mem,
        nodelist=nodelist,
        account=account,
        qos=qos,
        gpus=gpus,
    )


@mcp.tool(name="dispatch_srun")
def dispatch_srun(
    project: str,
    command: str,
    host: str | None = None,
    partition: str | None = None,
    cpus: int | None = None,
    time: str | None = None,
    mem: str | None = None,
    nodelist: str | None = None,
    account: str | None = None,
    qos: str | None = None,
    gpus: str | None = None,
) -> dict:
    """Run ``command`` synchronously inside an ``srun`` allocation.

    ``project`` is the directory under ``$SCITEX_HPC_REMOTE_BASE`` (default
    ``~/proj``) where the rsync'd source lives and where ``command`` runs.
    Returns ``{"returncode": <int>}``.
    """
    from .._dispatch import srun as _srun

    cfg = _make_config(
        project, command, host, partition, cpus, time, mem, nodelist, account, qos, gpus
    )
    return {"returncode": _srun(cfg)}


@mcp.tool(name="submit_sbatch")
def submit_sbatch(
    project: str,
    command: str,
    host: str | None = None,
    partition: str | None = None,
    cpus: int | None = None,
    time: str | None = None,
    mem: str | None = None,
    nodelist: str | None = None,
    account: str | None = None,
    qos: str | None = None,
    gpus: str | None = None,
) -> dict:
    """Submit ``command`` as a batch job. Returns ``{"job_id": "<id>"}`` or ``{"job_id": null}``."""
    from .._dispatch import sbatch as _sbatch

    cfg = _make_config(
        project, command, host, partition, cpus, time, mem, nodelist, account, qos, gpus
    )
    return {"job_id": _sbatch(cfg)}


@mcp.tool(name="sync_project")
def sync_project(project: str, host: str | None = None) -> dict:
    """rsync the local ``project`` directory to ``$SCITEX_HPC_REMOTE_BASE/<project>`` on ``host``."""
    from .._sync import sync as _sync

    cfg = _make_config(project=project, host=host)
    _sync(cfg)
    return {"project": project, "host": cfg.resolve("host")}


@mcp.tool()
def poll_job(job_id: str, host: str | None = None) -> dict:
    """Return ``sacct`` status for ``job_id``: ``state``, ``exit_code``, ``elapsed``."""
    from .._results import poll_job as _poll

    cfg = _make_config(project="", host=host)
    return _poll(cfg, job_id)


@mcp.tool()
def fetch_result(
    project: str,
    job_id: str,
    host: str | None = None,
    local_dir: str = ".pytest-hpc-output",
) -> dict:
    """scp the sbatch job's stdout file back to ``local_dir``. Returns ``{"ok": <bool>}``."""
    from .._results import fetch_result as _fetch

    cfg = _make_config(project=project, host=host)
    return {"ok": _fetch(cfg, job_id, local_dir=local_dir)}
