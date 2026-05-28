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


# ----------------------------------------------- name-aligned dispatch aliases
# Thin aliases whose MCP tool name matches the underlying Python API name
# (``sbatch``, ``srun``, ``sync``) verbatim. They sit alongside the verb-
# prefixed canonical wrappers above (``submit_sbatch``, ``dispatch_srun``,
# ``sync_project``) so existing umbrella callers keep working while the
# scitex-dev v0.13.0 audit-mcp-tools §6 rule -- which matches by exact
# name -- sees the Python API surface as fully covered.
#
# This closes the pre-existing 3/N gap that #8 worked around via the
# zero-growth principle (#9). After this PR, the gap on develop is 0/N.


@mcp.tool()
def sbatch(
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
    """Alias of ``submit_sbatch`` whose MCP name matches the Python API.

    Submit ``command`` as a batch job under ``project``. Returns
    ``{"job_id": "<id>"}`` (or ``{"job_id": null}`` on submission failure).
    """
    from .._dispatch import sbatch as _sbatch

    cfg = _make_config(
        project, command, host, partition, cpus, time, mem, nodelist, account, qos, gpus
    )
    return {"job_id": _sbatch(cfg)}


@mcp.tool()
def srun(
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
    """Alias of ``dispatch_srun`` whose MCP name matches the Python API.

    Run ``command`` synchronously inside an ``srun`` allocation under
    ``project``. Returns ``{"returncode": <int>}``.
    """
    from .._dispatch import srun as _srun

    cfg = _make_config(
        project, command, host, partition, cpus, time, mem, nodelist, account, qos, gpus
    )
    return {"returncode": _srun(cfg)}


@mcp.tool()
def sync(project: str, host: str | None = None) -> dict:
    """Alias of ``sync_project`` whose MCP name matches the Python API.

    rsync the local ``project`` directory to ``$SCITEX_HPC_REMOTE_BASE/<project>``
    on ``host``. Returns ``{"project": ..., "host": ...}``.
    """
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


# ------------------------------------------------------------ HPC-awareness
# Thin wrappers around the host-side env-modules helpers added in
# scitex-hpc#8 (Lmod / Tcl + Apptainer resolution). Tool names match the
# Python API names exactly so the scitex-dev audit-mcp-tools rule §6
# does not grow the existing coverage gap when these helpers ship --
# "zero-growth" per the operator directive on top of #8.


@mcp.tool()
def detect_module_system() -> dict:
    """Detect the host's env-modules implementation.

    Returns ``{"system": "lmod" | "tcl" | null}``. Probes ``$LMOD_CMD``,
    ``$MODULESHOME``, then ``module --version`` as a final sniff.
    """
    from .._modules import detect_module_system as _detect

    return {"system": _detect()}


@mcp.tool()
def module_load(modules: list[str], shell: str = "sh") -> dict:
    """``module load <modules>`` -> env-var diff dict.

    ``modules`` is the list of module names (e.g. ``["Apptainer/1.3.3"]``).
    Returns ``{"diff": {var: value, ...}}``. Caller can splat the diff
    into a subprocess env without spawning a login shell.

    Raises a tool-level error if ``module load`` exits non-zero.
    """
    from .._modules import module_load as _module_load

    diff = _module_load(*modules, shell=shell)
    return {"diff": diff}


@mcp.tool()
def load_apptainer(version: str | None = "1.3.3") -> dict:
    """Resolve an absolute path to the ``apptainer`` binary.

    When a module system is detected, loads ``Apptainer/<version>`` (or
    bare ``Apptainer`` when ``version=null``) first; then ``shutil.which``.
    Returns ``{"path": "<abs-path>"}``.
    """
    from .._modules import load_apptainer as _load

    return {"path": str(_load(version=version))}
