"""Tests for scitex_hpc._modules (Lmod / Tcl env-modules helpers).

Uses hand-rolled DI stubs on the ``_run`` / ``_env`` / ``_which`` kwargs of
``detect_module_system`` / ``module_load`` / ``load_apptainer`` -- no
``unittest.mock``, no ``monkeypatch``, no real ``module`` subprocess.

PA-306 (no mocks) is satisfied by dependency injection.
"""

from __future__ import annotations

import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pytest

from scitex_hpc._modules import (
    _parse_env_exports,
    detect_module_system,
    load_apptainer,
    module_load,
)

# ---------------------------------------------------------------------------
# Hand-rolled fakes (NOT mocks).
# ---------------------------------------------------------------------------


@dataclass
class _Result:
    """CompletedProcess-shaped result used by every test."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class _ScriptedRunner:
    """Returns a queued ``_Result`` per call; records every invocation."""

    results: List[_Result]
    calls: List[Tuple[tuple, dict]] = field(default_factory=list)

    def __call__(self, *args, **kwargs) -> _Result:
        self.calls.append((args, kwargs))
        if not self.results:
            return _Result(returncode=1, stderr="no scripted result")
        return self.results.pop(0)


def _runner(*results: _Result) -> _ScriptedRunner:
    """Build a runner that returns the given results in order."""
    return _ScriptedRunner(results=list(results))


# ---------------------------------------------------------------------------
# detect_module_system
# ---------------------------------------------------------------------------


def test_detect_module_system_returns_lmod_when_lmod_cmd_set():
    # Arrange
    env = {"LMOD_CMD": "/usr/share/lmod/lmod/libexec/lmod"}
    run = _runner()
    # Act
    system = detect_module_system(_env=env, _run=run)
    # Assert
    assert system == "lmod"


def test_detect_module_system_returns_lmod_when_moduleshome_plus_lmod_version():
    # Arrange
    env = {"MODULESHOME": "/usr/share/lmod/lmod", "LMOD_VERSION": "8.7.32"}
    run = _runner()
    # Act
    system = detect_module_system(_env=env, _run=run)
    # Assert
    assert system == "lmod"


def test_detect_module_system_returns_tcl_when_only_moduleshome_set():
    # Arrange
    env = {"MODULESHOME": "/usr/share/modules"}
    run = _runner()
    # Act
    system = detect_module_system(_env=env, _run=run)
    # Assert
    assert system == "tcl"


def test_detect_module_system_returns_none_when_command_fails():
    # Arrange
    env: dict = {}
    run = _runner(_Result(returncode=127, stderr="module: command not found"))
    # Act
    system = detect_module_system(_env=env, _run=run)
    # Assert
    assert system is None


def test_detect_module_system_returns_lmod_from_command_output():
    # Arrange
    env: dict = {}
    run = _runner(_Result(returncode=0, stdout="Modules based on Lmod Version 8.7"))
    # Act
    system = detect_module_system(_env=env, _run=run)
    # Assert
    assert system == "lmod"


def test_detect_module_system_returns_tcl_from_command_output():
    # Arrange
    env: dict = {}
    run = _runner(_Result(returncode=0, stdout="Modules Release 5.3.1\n"))
    # Act
    system = detect_module_system(_env=env, _run=run)
    # Assert
    assert system == "tcl"


def test_detect_module_system_skips_subprocess_when_env_resolves():
    # Arrange
    env = {"LMOD_CMD": "/opt/lmod"}
    run = _runner()
    # Act
    detect_module_system(_env=env, _run=run)
    # Assert
    assert run.calls == []


# ---------------------------------------------------------------------------
# module_load
# ---------------------------------------------------------------------------


def test_module_load_returns_parsed_export_diff():
    # Arrange
    stdout = (
        "export PATH=/opt/apptainer/1.3.3/bin:/usr/bin;\n"
        "export APPTAINER_HOME=/opt/apptainer/1.3.3;\n"
    )
    run = _runner(_Result(returncode=0, stdout=stdout))
    # Act
    diff = module_load("Apptainer/1.3.3", _run=run)
    # Assert
    assert diff == {
        "PATH": "/opt/apptainer/1.3.3/bin:/usr/bin",
        "APPTAINER_HOME": "/opt/apptainer/1.3.3",
    }


def test_module_load_raises_module_not_found_on_failure():
    # Arrange
    run = _runner(_Result(returncode=1, stderr="Lmod has detected: ERROR"))

    # Act
    def _call():
        module_load("Apptainer/9.9.9", _run=run)

    # Assert
    with pytest.raises(ModuleNotFoundError, match="Lmod has detected"):
        _call()


def test_module_load_raises_value_error_on_empty_module_list():
    # Arrange
    run = _runner()

    # Act
    def _call():
        module_load(_run=run)

    # Assert
    with pytest.raises(ValueError, match="at least one module name"):
        _call()


def test_module_load_invokes_runner_with_correct_shell_flag():
    # Arrange
    run = _runner(_Result(returncode=0, stdout=""))
    # Act
    module_load("Apptainer/1.3.3", shell="bash", _run=run)
    # Assert
    assert "module --shell bash load Apptainer/1.3.3" in run.calls[0][0][0][-1]


# ---------------------------------------------------------------------------
# _parse_env_exports
# ---------------------------------------------------------------------------


def test_parse_env_exports_handles_simple_export_line():
    # Arrange
    blob = "export PATH=/opt/x/bin;"
    # Act
    diff = _parse_env_exports(blob)
    # Assert
    assert diff == {"PATH": "/opt/x/bin"}


def test_parse_env_exports_strips_single_quoted_value():
    # Arrange
    blob = "export FOO='bar baz';"
    # Act
    diff = _parse_env_exports(blob)
    # Assert
    assert diff["FOO"] == "bar baz"


def test_parse_env_exports_handles_setenv_tcl_form():
    # Arrange
    blob = "setenv APPTAINER_HOME /opt/apptainer/1.3.3"
    # Act
    diff = _parse_env_exports(blob)
    # Assert
    assert diff == {"APPTAINER_HOME": "/opt/apptainer/1.3.3"}


def test_parse_env_exports_skips_comment_lines():
    # Arrange
    blob = "# Lmod\nexport PATH=/x;\n# end\n"
    # Act
    diff = _parse_env_exports(blob)
    # Assert
    assert diff == {"PATH": "/x"}


def test_parse_env_exports_preserves_empty_exported_value():
    # Arrange
    blob = "export FOO=;"
    # Act
    diff = _parse_env_exports(blob)
    # Assert
    assert diff == {"FOO": ""}


# ---------------------------------------------------------------------------
# load_apptainer
# ---------------------------------------------------------------------------


class _FakeWhich:
    """Hand-rolled shutil.which-shaped callable that returns canned paths."""

    def __init__(self, returns: Optional[str]):
        self._returns = returns
        self.calls: List[str] = []

    def __call__(self, name: str) -> Optional[str]:
        self.calls.append(name)
        return self._returns


def test_load_apptainer_falls_back_to_which_when_no_module_system():
    # Arrange
    env: dict = {}
    run = _runner(_Result(returncode=127, stderr="module: not found"))
    which = _FakeWhich(returns="/usr/bin/apptainer")
    # Act
    path = load_apptainer(_env=env, _run=run, _which=which)
    # Assert
    assert path == pathlib.Path("/usr/bin/apptainer").resolve()


def test_load_apptainer_loads_module_when_lmod_detected():
    # Arrange
    env = {"LMOD_CMD": "/opt/lmod"}
    run = _runner(
        _Result(returncode=0, stdout="export PATH=/opt/apptainer/1.3.3/bin;"),
    )
    which = _FakeWhich(returns="/opt/apptainer/1.3.3/bin/apptainer")
    # Act
    load_apptainer(version="1.3.3", _env=env, _run=run, _which=which)
    # Assert
    assert env["PATH"] == "/opt/apptainer/1.3.3/bin"


def test_load_apptainer_raises_runtime_error_when_binary_missing():
    # Arrange
    env: dict = {}
    run = _runner(_Result(returncode=127))
    which = _FakeWhich(returns=None)

    # Act
    def _call():
        load_apptainer(_env=env, _run=run, _which=which)

    # Assert
    with pytest.raises(RuntimeError, match="apptainer binary not found"):
        _call()


def test_load_apptainer_uses_unversioned_module_name_when_version_is_none():
    # Arrange
    env = {"LMOD_CMD": "/opt/lmod"}
    run = _runner(_Result(returncode=0, stdout=""))
    which = _FakeWhich(returns="/opt/apptainer/bin/apptainer")
    # Act
    load_apptainer(version=None, _env=env, _run=run, _which=which)
    # Assert
    assert "module --shell sh load Apptainer" in run.calls[0][0][0][-1]
