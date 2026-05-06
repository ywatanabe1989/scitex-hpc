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
