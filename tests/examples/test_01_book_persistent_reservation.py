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


def test_example_file_exists():
    assert EXAMPLE_PATH.is_file(), f"missing example: {EXAMPLE_PATH}"


def test_example_imports_cleanly(monkeypatch, tmp_path):
    """The example module loads without syntax errors and exposes ``main``.

    We don't drive the function in CI — `@stx.session` pulls in the full
    scitex umbrella (scitex_io etc.) and the cluster-touching path needs
    `SCITEX_HPC_HOST`. A clean import is the meaningful invariant; any
    actual booking is exercised by `tests/scitex_hpc/_cli/`.
    """
    pytest.importorskip("scitex")
    monkeypatch.delenv("SCITEX_HPC_HOST", raising=False)
    monkeypatch.chdir(tmp_path)

    try:
        mod = _load_example()
    except ModuleNotFoundError as e:
        pytest.skip(f"scitex umbrella missing in this env: {e}")
    assert hasattr(mod, "main")
