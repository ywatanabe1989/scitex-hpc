"""Lmod / Tcl env-modules helpers for HPC hosts.

Why this exists
---------------
Many HPC sites (Spartan, NCI Gadi, OLCF, ...) ship Apptainer /
Singularity / CUDA / GCC via the environment-modules system rather
than as system-wide binaries on PATH. A naive
``shutil.which("apptainer")`` on a Spartan compute node returns
either nothing or a 1KB bash shim that itself calls
``module load Apptainer/1.3.3`` -- which fails in any context where
the Lmod ``module`` function is not in the active shell environment
(sac's inside-SIF nested launch, plain ``subprocess.run([...])`` from
a Python script that did not start from a login shell, etc.).

This module gives sac (and any other host-side scitex-hpc caller) a
real, testable, no-mocks path from ``load_apptainer()`` to an absolute
executable path: detect Lmod / Tcl modules, invoke
``module --shell sh load <name>`` as a subprocess, parse the env-var
diff out of stdout, splat it into the caller's process env, then
``shutil.which`` the target binary against the freshly-loaded PATH.

Design rationale + the operator decision that motivated it live in
~/proj/scitex-lead/GITIGNORED/FUTURE/scitex-hpc-module-load-helpers.md
(operator hint msg 6705, parallel-development greenlight msg 6709,
2026-05-28).

Test seam
---------
Every public function accepts a ``_run`` kwarg (default
``subprocess.run`` via ``_default_run``) and ``load_apptainer`` also
takes ``_which`` and ``_env``; unit tests inject hand-rolled stub
callables returning canned stdout / stderr -- no ``unittest.mock``,
no ``monkeypatch``, no real ``module`` subprocess on CI. PA-306 (no
mocks) is satisfied by dependency injection; this mirrors the
``runner=`` pattern in ``scitex_hpc._dispatch``.

Layered with
------------
scitex-agent-container ships Apptainer inside the sac-base SIF
(scitex-agent-container#239, 2026-05-28). That bundle covers
inside-SIF nested launches; this module covers the complementary
Layer 1 case -- host-side sac on an HPC login / compute node where
Apptainer is only reachable via ``module load``.
"""

from __future__ import annotations

import os
import pathlib
import shlex
import shutil
import subprocess
from typing import Callable, Dict, Literal, Optional

# ---------------------------------------------------------------------------
# Test seam: a subprocess.run-shaped callable.
# ---------------------------------------------------------------------------

_RunCallable = Callable[..., subprocess.CompletedProcess]


def _default_run(*args, **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` with capture + text + non-raising defaults."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    return subprocess.run(*args, **kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_module_system(
    *,
    _env: Optional[Dict[str, str]] = None,
    _run: _RunCallable = _default_run,
) -> Optional[Literal["lmod", "tcl"]]:
    """Detect which env-modules implementation (if any) is reachable.

    Probes in order, cheapest first.
    First, ``$LMOD_CMD`` (set by Lmod's init script).
    Second, ``$MODULESHOME`` *and* ``$LMOD_VERSION`` (Lmod sets both).
    Third, ``$MODULESHOME`` alone (classic Tcl environment-modules).
    Last, ``bash -lc 'module --version'`` as a one-shot subprocess
    sniff for sites that strip LMOD_CMD inside batch scripts.

    Returns ``"lmod"``, ``"tcl"``, or ``None``. Pure-env detection
    short-circuits before any subprocess is spawned.
    """
    env = _env if _env is not None else os.environ

    if env.get("LMOD_CMD"):
        return "lmod"
    if env.get("MODULESHOME") and env.get("LMOD_VERSION"):
        return "lmod"
    if env.get("MODULESHOME"):
        return "tcl"

    result = _run(["bash", "-lc", "module --version 2>&1"])
    if getattr(result, "returncode", 1) != 0:
        return None
    blob = (
        (getattr(result, "stdout", "") or "") + (getattr(result, "stderr", "") or "")
    ).lower()
    if "lmod" in blob:
        return "lmod"
    if "modules release" in blob or "modules-tcl" in blob or "tcl" in blob:
        return "tcl"
    return None


def module_load(
    *modules: str,
    shell: str = "sh",
    _run: _RunCallable = _default_run,
) -> Dict[str, str]:
    """Load env-modules and return the env-var diff they introduced.

    Invokes ``module --shell <shell> load <modules>``, which prints a
    shell-eval-able script (``export PATH=...; export FOO=bar;``).
    We parse that into ``{var: value}`` and return the diff -- callers
    can splat it into a subprocess env, or merge into ``os.environ``,
    without having to spawn a login shell themselves.

    ``shell`` (default ``sh``) only changes the output language Lmod
    emits. We always parse the ``export VAR=VALUE`` / ``setenv VAR
    VALUE`` forms -- valid for both ``sh`` and ``bash`` outputs.

    Raises ``ValueError`` if no modules were named, and
    ``ModuleNotFoundError`` if the underlying ``module load`` exits
    non-zero -- with the module's stderr surfaced verbatim.
    """
    if not modules:
        raise ValueError("module_load() requires at least one module name")

    mods = " ".join(shlex.quote(m) for m in modules)
    cmd = f"module --shell {shlex.quote(shell)} load {mods}"
    result = _run(["bash", "-lc", cmd])
    if getattr(result, "returncode", 1) != 0:
        stderr = (getattr(result, "stderr", "") or "").strip() or "no stderr"
        raise ModuleNotFoundError(f"module load {list(modules)!r} failed: {stderr}")
    return _parse_env_exports(getattr(result, "stdout", "") or "")


def load_apptainer(
    *,
    version: Optional[str] = "1.3.3",
    _env: Optional[Dict[str, str]] = None,
    _run: _RunCallable = _default_run,
    _which: Callable[[str], Optional[str]] = shutil.which,
) -> pathlib.Path:
    """Resolve an absolute path to the ``apptainer`` binary.

    When a module system is detected, attempt
    ``module load Apptainer/<version>`` (or just ``Apptainer`` if
    ``version`` is None), splat the resulting env-var diff into the
    caller's process env, then ``shutil.which("apptainer")`` against
    the post-load environment.
    Otherwise fall back to a bare ``shutil.which("apptainer")`` so
    non-HPC dev laptops with apptainer already on PATH continue to
    Just Work.

    The function mutates the env dict passed via ``_env`` (default
    ``os.environ``); production callers get sticky PATH updates,
    tests pass an isolated dict and assert no leakage.

    Raises ``RuntimeError`` if no apptainer binary is reachable even
    after the module load.
    """
    env = _env if _env is not None else os.environ
    system = detect_module_system(_env=env, _run=_run)

    if system is not None:
        mod = "Apptainer" if version is None else f"Apptainer/{version}"
        diff = module_load(mod, _run=_run)
        for k, v in diff.items():
            env[k] = v

    path = _which("apptainer")
    if path is None:
        raise RuntimeError(
            "apptainer binary not found after module load; "
            f"module system detected: {system!r}. "
            "Either install apptainer or pass an explicit version= "
            "matching one of `module avail Apptainer`."
        )
    return pathlib.Path(path).resolve()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_env_exports(blob: str) -> Dict[str, str]:
    """Pick ``export VAR=VALUE`` / ``setenv VAR VALUE`` pairs out of *blob*.

    Tolerant of trailing semicolons, single- and double-quoted values,
    and blank / commented lines. Variables exported empty (e.g.
    ``export FOO=''``) are preserved. ``unset`` lines are skipped --
    Lmod emits them but they are rarely the difference between
    found / not-found for the apptainer-resolution use case.
    """
    diff: Dict[str, str] = {}
    for raw in blob.splitlines():
        line = raw.strip().rstrip(";").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            body = line[len("export ") :]
            if "=" not in body:
                continue
            var, _, value = body.partition("=")
        elif line.startswith("setenv "):
            parts = line[len("setenv ") :].split(None, 1)
            if len(parts) != 2:
                continue
            var, value = parts
        else:
            continue
        var = var.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        diff[var] = value
    return diff
