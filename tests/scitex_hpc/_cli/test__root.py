"""Smoke tests for the scitex-hpc CLI (argparse plumbing + JSON output).

Uses ``Reservation._override_defaults`` (real module-attribute mutation in
a context manager) to inject hand-rolled fake runners — no ``monkeypatch``
fixture, no mocks.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from scitex_hpc import Reservation
from scitex_hpc import _reservation as resmod
from scitex_hpc._cli import main

# ---------------------------------------------------------------------------
# Hand-rolled fakes
# ---------------------------------------------------------------------------


@dataclass
class _Result:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class _FakeRunner:
    """Records every (host, command) call and returns scripted results.

    ``dispatcher(command) -> _Result`` lets tests vary the answer based on
    the command body (e.g. ``sbatch`` vs ``squeue`` vs ``scancel``). When
    no dispatcher is given, every call returns ``default``.
    """

    def __init__(self, *, default: _Result | None = None, dispatcher=None):
        self.calls: list[tuple[str, str]] = []
        self._default = default if default is not None else _Result()
        self._dispatcher = dispatcher

    def __call__(self, host, command, *, check=False, timeout=None):
        self.calls.append((host, command))
        if self._dispatcher is not None:
            return self._dispatcher(command)
        return self._default

    @property
    def commands(self) -> list[str]:
        return [c for _, c in self.calls]


def _noop_sleep(_seconds):
    """Real no-op callable for the sleep seam — not a mock."""
    return None


# ---------------------------------------------------------------------------
# Fixtures (real env-var mutation, yield-based teardown — no monkeypatch)
# ---------------------------------------------------------------------------


@pytest.fixture
def lease_dir(tmp_path: Path):
    """Isolate ``SCITEX_HPC_LEASE_DIR`` per test via real env-var mutation."""
    # Arrange
    d = tmp_path / "leases"
    key = "SCITEX_HPC_LEASE_DIR"
    prior = os.environ.get(key)
    os.environ[key] = str(d)
    try:
        # Act / Assert — yielded to the test body.
        yield d
    finally:
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


# ---------------------------------------------------------------------------
# `reservations list`
# ---------------------------------------------------------------------------


class TestList:
    def test_list_emits_empty_marker_when_no_leases(self, lease_dir, capsys):
        # Arrange
        # (lease_dir is empty)
        # Act
        rc = main(["reservations", "list"])
        # Assert
        assert rc == 0 and "(no reservations)" in capsys.readouterr().out

    def test_list_json_emits_saved_lease(self, lease_dir, capsys):
        # Arrange
        Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            node="spartan-bm022.hpc",
        ).save()
        # Act
        main(["reservations", "list", "--json"])
        # Assert
        out = json.loads(capsys.readouterr().out)
        assert out[0]["id"] == "spartan-foo" and out[0]["job_id"] == "42"

    def test_list_table_shows_persistent_column(self, lease_dir, capsys):
        # Arrange
        Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            node="n1",
            persistent=True,
        ).save()
        # Act
        main(["reservations", "list"])
        # Assert
        out = capsys.readouterr().out
        assert "spartan-foo" in out and "yes" in out


# ---------------------------------------------------------------------------
# `reservations get`
# ---------------------------------------------------------------------------


class TestGet:
    def test_get_returns_2_when_lease_is_missing(self, lease_dir):
        # Arrange
        # (lease_dir is empty)
        # Act
        rc = main(["reservations", "get", "nope"])
        # Assert
        assert rc == 2

    def test_get_emits_json_for_saved_lease(self, lease_dir, capsys):
        # Arrange
        Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42").save()
        # Act
        rc = main(["reservations", "get", "spartan-foo"])
        # Assert
        assert rc == 0 and json.loads(capsys.readouterr().out)["job_id"] == "42"


# ---------------------------------------------------------------------------
# `reservations exec`
# ---------------------------------------------------------------------------


class TestExec:
    def test_exec_propagates_remote_returncode(self, lease_dir, capsys):
        # Arrange
        Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42").save()
        runner = _FakeRunner(
            default=_Result(returncode=7, stdout="hi\n", stderr="err\n")
        )
        # Act
        with resmod._override_defaults(runner=runner, sleep=_noop_sleep):
            rc = main(["reservations", "exec", "spartan-foo", "echo hi"])
        # Assert
        captured = capsys.readouterr()
        assert rc == 7 and "hi" in captured.out and "err" in captured.err


# ---------------------------------------------------------------------------
# `reservations release / cancel`
# ---------------------------------------------------------------------------


class TestRelease:
    def test_release_missing_lease_is_idempotent(self, lease_dir):
        # Arrange
        # (lease_dir is empty)
        # Act
        rc = main(["reservations", "release", "nope"])
        # Assert
        assert rc == 0

    def test_release_invokes_scancel_with_job_id(self, lease_dir):
        # Arrange
        Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42").save()
        runner = _FakeRunner(default=_Result(returncode=0))
        # Act
        with resmod._override_defaults(runner=runner, sleep=_noop_sleep):
            rc = main(["reservations", "release", "spartan-foo"])
        # Assert
        assert rc == 0 and any("scancel 42" in c for c in runner.commands)


# ---------------------------------------------------------------------------
# `reservations book` smoke
# ---------------------------------------------------------------------------


class TestBookSmoke:
    def test_book_subcommand_returns_job_id_and_node(self, lease_dir, capsys):
        # Arrange
        def dispatch(command: str) -> _Result:
            if "sbatch" in command:
                return _Result(stdout="Submitted batch job 99\n")
            return _Result(stdout="RUNNING n1\n")

        runner = _FakeRunner(dispatcher=dispatch)
        # Act
        with resmod._override_defaults(
            runner=runner, sleep=_noop_sleep, monotonic=lambda: 0.0
        ):
            rc = main(
                [
                    "reservations",
                    "book",
                    "dev-pool",
                    "--host",
                    "spartan",
                    "--cpus",
                    "4",
                    "--time",
                    "1-0",
                    "--mem",
                    "8G",
                    "--json",
                ]
            )
        # Assert
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and out["job_id"] == "99" and out["node"] == "n1"


# ---------------------------------------------------------------------------
# `reservations book --tmux-server`
# ---------------------------------------------------------------------------


class TestBookTmuxServer:
    """`--tmux-server` flag wires through to ``Reservation.book``."""

    def test_book_tmux_server_flag_appears_in_sbatch_script(self, lease_dir, capsys):
        # Arrange
        captured_commands: list[str] = []

        def dispatch(command: str) -> _Result:
            captured_commands.append(command)
            if "sbatch" in command:
                return _Result(stdout="Submitted batch job 42\n")
            return _Result(stdout="RUNNING n1\n")

        runner = _FakeRunner(dispatcher=dispatch)
        # Act
        with resmod._override_defaults(
            runner=runner, sleep=_noop_sleep, monotonic=lambda: 0.0
        ):
            rc = main(
                [
                    "reservations",
                    "book",
                    "test",
                    "--host",
                    "spartan",
                    "--tmux-server",
                    "sac",
                ]
            )
        # Assert
        sbatch_calls = [c for c in captured_commands if "sbatch" in c]
        assert rc == 0 and any("tmux -L sac" in c for c in sbatch_calls)

    def test_book_without_tmux_server_omits_tmux_bootstrap(self, lease_dir, capsys):
        # Arrange
        captured_commands: list[str] = []

        def dispatch(command: str) -> _Result:
            captured_commands.append(command)
            if "sbatch" in command:
                return _Result(stdout="Submitted batch job 42\n")
            return _Result(stdout="RUNNING n1\n")

        runner = _FakeRunner(dispatcher=dispatch)
        # Act
        with resmod._override_defaults(
            runner=runner, sleep=_noop_sleep, monotonic=lambda: 0.0
        ):
            main(["reservations", "book", "test", "--host", "spartan"])
        # Assert
        sbatch_calls = [c for c in captured_commands if "sbatch" in c]
        assert all("tmux -L" not in c for c in sbatch_calls)


# ---------------------------------------------------------------------------
# `reservations refresh`
# ---------------------------------------------------------------------------


class TestRefresh:
    """`reservations refresh` re-discovers job_id by friendly name."""

    def test_refresh_picks_up_new_jobid_via_squeue(self, lease_dir, capsys):
        # Arrange
        Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="100",
            node="bm022",
        ).save()
        runner = _FakeRunner(default=_Result(stdout="200 RUNNING bm175\n"))
        # Act
        with resmod._override_defaults(runner=runner, sleep=_noop_sleep):
            rc = main(["reservations", "refresh", "spartan-foo", "--json"])
        # Assert
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and out["job_id"] == "200" and out["node"] == "bm175"

    def test_refresh_returns_2_when_no_live_job_in_queue(self, lease_dir, capsys):
        # Arrange
        Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42").save()
        runner = _FakeRunner(default=_Result(stdout=""))
        # Act
        with resmod._override_defaults(runner=runner, sleep=_noop_sleep):
            rc = main(["reservations", "refresh", "spartan-foo"])
        # Assert
        captured = capsys.readouterr()
        assert rc == 2 and "no live job found" in captured.err

    def test_refresh_raises_keyerror_when_lease_missing(self, lease_dir):
        # Arrange
        # (lease_dir is empty)
        # Act
        action = lambda: main(["reservations", "refresh", "nonexistent"])
        # Assert
        with pytest.raises(KeyError, match="no reservation"):
            action()
