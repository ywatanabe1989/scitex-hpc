"""Smoke test for the scitex-hpc MCP server module."""

from __future__ import annotations

import pytest


def test_mcp_server_module_imports_cleanly():
    # Arrange
    pytest.importorskip("fastmcp")

    # Act
    from scitex_hpc._mcp.server import mcp

    # Assert
    assert mcp is not None


def test_mcp_server_has_expected_name():
    # Arrange
    pytest.importorskip("fastmcp")
    from scitex_hpc._mcp.server import mcp

    # Act
    name = getattr(mcp, "name", None)

    # Assert
    assert name == "scitex-hpc"


def test_mcp_skills_list_tool_registered():
    """`hpc_skills_list` must be registered (§5)."""
    # Arrange
    pytest.importorskip("fastmcp")
    import asyncio

    from scitex_hpc._mcp.server import mcp

    # Act
    tools = asyncio.run(mcp.list_tools())
    names = {getattr(t, "name", None) for t in tools}

    # Assert
    assert "skills_list" in names or "hpc_skills_list" in names


def test_mcp_skills_get_tool_registered():
    """`hpc_skills_get` must be registered (§5)."""
    # Arrange
    pytest.importorskip("fastmcp")
    import asyncio

    from scitex_hpc._mcp.server import mcp

    # Act
    tools = asyncio.run(mcp.list_tools())
    names = {getattr(t, "name", None) for t in tools}

    # Assert
    assert "skills_get" in names or "hpc_skills_get" in names


def test_mcp_detect_module_system_tool_registered():
    # Arrange
    pytest.importorskip("fastmcp")
    import asyncio

    from scitex_hpc._mcp.server import mcp

    # Act
    tools = asyncio.run(mcp.list_tools())
    names = {getattr(t, "name", None) for t in tools}

    # Assert
    assert "detect_module_system" in names


def test_mcp_module_load_tool_registered():
    # Arrange
    pytest.importorskip("fastmcp")
    import asyncio

    from scitex_hpc._mcp.server import mcp

    # Act
    tools = asyncio.run(mcp.list_tools())
    names = {getattr(t, "name", None) for t in tools}

    # Assert
    assert "module_load" in names


def test_mcp_load_apptainer_tool_registered():
    # Arrange
    pytest.importorskip("fastmcp")
    import asyncio

    from scitex_hpc._mcp.server import mcp

    # Act
    tools = asyncio.run(mcp.list_tools())
    names = {getattr(t, "name", None) for t in tools}

    # Assert
    assert "load_apptainer" in names


def test_mcp_sbatch_alias_tool_registered():
    # Arrange
    pytest.importorskip("fastmcp")
    import asyncio

    from scitex_hpc._mcp.server import mcp

    # Act
    tools = asyncio.run(mcp.list_tools())
    names = {getattr(t, "name", None) for t in tools}

    # Assert
    assert "sbatch" in names


def test_mcp_srun_alias_tool_registered():
    # Arrange
    pytest.importorskip("fastmcp")
    import asyncio

    from scitex_hpc._mcp.server import mcp

    # Act
    tools = asyncio.run(mcp.list_tools())
    names = {getattr(t, "name", None) for t in tools}

    # Assert
    assert "srun" in names


def test_mcp_sync_alias_tool_registered():
    # Arrange
    pytest.importorskip("fastmcp")
    import asyncio

    from scitex_hpc._mcp.server import mcp

    # Act
    tools = asyncio.run(mcp.list_tools())
    names = {getattr(t, "name", None) for t in tools}

    # Assert
    assert "sync" in names
