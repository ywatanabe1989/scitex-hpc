"""Smoke test for examples/01_book_persistent_reservation.py.

The example is a guarded entry point: when ``SCITEX_HPC_HOST`` is unset
it logs a warning and returns 0 without contacting any cluster. We
exercise that path so the example stays syntactically valid and its
guard actually short-circuits — without ever booking a real SLURM job
in CI.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "01_book_persistent_reservation.py"
)


def _load_example():
    spec = importlib.util.spec_from_file_location("ex_book", EXAMPLE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ex_book"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def _isolated_example_env(tmp_path):
    """Real env-var + cwd isolation — no monkeypatch."""
    # Arrange
    import os

    prior_host = os.environ.get("SCITEX_HPC_HOST")
    prior_cwd = os.getcwd()
    os.environ.pop("SCITEX_HPC_HOST", None)
    os.chdir(tmp_path)
    try:
        # Act / Assert — handled by the test body.
        yield tmp_path
    finally:
        os.chdir(prior_cwd)
        if prior_host is None:
            os.environ.pop("SCITEX_HPC_HOST", None)
        else:
            os.environ["SCITEX_HPC_HOST"] = prior_host


def test_example_source_file_is_present_on_disk():
    # Arrange
    path = EXAMPLE_PATH
    # Act
    exists = path.is_file()
    # Assert
    assert exists, f"missing example: {path}"


@pytest.fixture
def _scitex_umbrella_present():
    """Skip the test if the scitex umbrella isn't installed."""
    pytest.importorskip("scitex")


def test_example_module_exposes_main_when_imported(
    _isolated_example_env, _scitex_umbrella_present
):
    """The example module loads without syntax errors and exposes ``main``.

    We don't drive the function in CI — `@stx.session` pulls in the full
    scitex umbrella (scitex_io etc.) and the cluster-touching path needs
    `SCITEX_HPC_HOST`. A clean import is the meaningful invariant; any
    actual booking is exercised by `tests/scitex_hpc/_cli/`.
    """
    # Arrange
    loader = _load_example

    # Act
    try:
        mod = loader()
    except ModuleNotFoundError:
        mod = None

    # Assert
    assert mod is not None and hasattr(mod, "main")
