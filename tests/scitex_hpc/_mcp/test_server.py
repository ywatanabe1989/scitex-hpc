"""Smoke test for the scitex-hpc MCP server module."""

from __future__ import annotations

import pytest


def test_mcp_server_importable():
    pytest.importorskip("fastmcp")
    from scitex_hpc._mcp.server import mcp

    assert mcp is not None
    assert getattr(mcp, "name", None) == "scitex-hpc"


def test_mcp_skills_tools_registered():
    """`hpc_skills_list` / `hpc_skills_get` must be registered (§5)."""
    pytest.importorskip("fastmcp")
    import asyncio

    from scitex_hpc._mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {getattr(t, "name", None) for t in tools}
    assert "skills_list" in names or "hpc_skills_list" in names
    assert "skills_get" in names or "hpc_skills_get" in names
