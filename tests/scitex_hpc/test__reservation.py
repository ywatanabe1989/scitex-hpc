"""Tests for scitex_hpc.Reservation.

Uses DI seams (``runner=``, ``attach_runner=``, ``sleep=``, ``monotonic=``
on ``Reservation.book`` / ``from_jobid`` / ``with_collaborators``) and a
real ``os.environ`` fixture for the lease directory. No mocks anywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scitex_hpc import JobConfig, Reservation
from scitex_hpc import _reservation as resmod


# ---------------------------------------------------------------------------
# Real fakes (hand-rolled, not mocks)
# ---------------------------------------------------------------------------


class _SSHResult:
    """Hand-rolled fake for scitex_ssh.SSHResult / CompletedProcess shape."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _proc(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Build a CompletedProcess-shaped result (returncode, stdout, stderr)."""
    return _SSHResult(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRunner:
    """Hand-rolled fake runner for the ``runner=`` DI seam.

    Records (host, command) pairs; returns scripted results, falls back
    to ``default`` for un-scripted calls. The recorded ``calls`` list lets
    tests assert on what the production code asked the collaborator to do.
    """

    def __init__(self, *, scripted=None, default=None, dispatcher=None):
        self.calls: list[tuple[str, str]] = []
        self._scripted = list(scripted) if scripted else []
        self._default = default if default is not None else _proc()
        self._dispatcher = dispatcher

    def __call__(self, host, command, *, check=False, timeout=None):
        self.calls.append((host, command))
        if self._dispatcher is not None:
            return self._dispatcher(host, command)
        if self._scripted:
            return self._scripted.pop(0)
        return self._default

    @property
    def commands(self) -> list[str]:
        return [c for _, c in self.calls]


class FakeAttachRunner:
    """Hand-rolled fake for the ``attach_runner=`` DI seam (local subprocess)."""

    def __init__(self, *, returncode: int = 0):
        self.calls: list[list[str]] = []
        self._returncode = returncode

    def __call__(self, args):
        self.calls.append(list(args))
        return _proc(returncode=self._returncode)


def _noop_sleep(_seconds):
    """Real callable — not a mock — used for the ``sleep=`` seam."""
    return None


def _clock(values):
    """Build a real callable returning successive values for the ``monotonic=`` seam."""
    it = iter(values)

    def _tick():
        return next(it)

    return _tick


# ---------------------------------------------------------------------------
# Fixtures (real env-var mutation, yield-based teardown — no monkeypatch)
# ---------------------------------------------------------------------------


@pytest.fixture
def lease_dir(tmp_path: Path):
    """Isolate the lease directory and the scitex-state root per test.

    Sets ``SCITEX_HPC_LEASE_DIR`` and ``SCITEX_DIR`` to ``tmp_path``
    locations, restoring the prior values on teardown. Real ``os.environ``
    mutation — no mocks.
    """
    # Arrange
    d = tmp_path / "leases"
    keys = ("SCITEX_HPC_LEASE_DIR", "SCITEX_DIR", "SCITEX_HPC_HOST")
    prior = {k: os.environ.get(k) for k in keys}
    os.environ["SCITEX_HPC_LEASE_DIR"] = str(d)
    os.environ["SCITEX_DIR"] = str(tmp_path / "scitex")
    # Unset HOST env so JobConfig(host="") resolves to "" (no user bleed-through).
    os.environ.pop("SCITEX_HPC_HOST", None)
    try:
        # Act / Assert — yield to the test body.
        yield d
    finally:
        # Restore prior env on teardown.
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _book_with(cfg, runner: FakeRunner, **kwargs):
    """Helper: call Reservation.book() with the fake runner + no-op sleep."""
    return Reservation.book(
        cfg,
        runner=runner,
        sleep=_noop_sleep,
        monotonic=_clock([0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5]),
        **kwargs,
    )


def _running_reservation(runner: FakeRunner | None = None) -> Reservation:
    """Build a saved RUNNING reservation with collaborators bound."""
    res = Reservation(
        id="spartan-foo",
        name="foo",
        host="spartan",
        job_id="42",
        node="spartan-bm022.hpc",
    )
    if runner is not None:
        res.with_collaborators(runner=runner, sleep=_noop_sleep)
    return res


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_writes_lease_file_to_disk(self, lease_dir):
        # Arrange
        res = Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            node="spartan-bm022.hpc",
            persistent=True,
        )
        # Act
        res.save()
        # Assert
        assert (lease_dir / "spartan-foo.json").is_file()

    def test_get_returns_loaded_reservation_after_save(self, lease_dir):
        # Arrange
        Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            node="spartan-bm022.hpc",
            persistent=True,
        ).save()
        # Act
        loaded = Reservation.get("spartan-foo")
        # Assert
        assert loaded is not None

    def test_get_preserves_job_id_through_round_trip(self, lease_dir):
        # Arrange
        Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
        ).save()
        # Act
        loaded = Reservation.get("spartan-foo")
        # Assert
        assert loaded.job_id == "42"

    def test_get_preserves_persistent_flag_through_round_trip(self, lease_dir):
        # Arrange
        Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            persistent=True,
        ).save()
        # Act
        loaded = Reservation.get("spartan-foo")
        # Assert
        assert loaded.persistent is True

    def test_get_by_friendly_name_returns_first_match(self, lease_dir):
        # Arrange
        Reservation(id="spartan-foo", name="foo", host="spartan", job_id="1").save()
        Reservation(id="cedar-foo", name="foo", host="cedar", job_id="2").save()
        # Act
        loaded = Reservation.get("foo")
        # Assert
        assert loaded is not None

    def test_get_by_friendly_name_with_host_filter_picks_spartan(self, lease_dir):
        # Arrange
        Reservation(id="spartan-foo", name="foo", host="spartan", job_id="1").save()
        Reservation(id="cedar-foo", name="foo", host="cedar", job_id="2").save()
        # Act
        loaded = Reservation.get("foo", host="spartan")
        # Assert
        assert loaded.job_id == "1"

    def test_get_by_friendly_name_with_host_filter_picks_cedar(self, lease_dir):
        # Arrange
        Reservation(id="spartan-foo", name="foo", host="spartan", job_id="1").save()
        Reservation(id="cedar-foo", name="foo", host="cedar", job_id="2").save()
        # Act
        loaded = Reservation.get("foo", host="cedar")
        # Assert
        assert loaded.job_id == "2"

    def test_get_returns_none_when_missing(self, lease_dir):
        # Arrange
        # (no reservations saved)
        # Act
        result = Reservation.get("nope")
        # Assert
        assert result is None

    def test_require_raises_keyerror_when_missing(self, lease_dir):
        # Arrange
        # (no reservations saved)
        # Act
        # Assert
        with pytest.raises(KeyError, match="no reservation"):
            Reservation.require("nope")

    def test_list_returns_empty_when_no_leases_exist(self, lease_dir):
        # Arrange
        # (no reservations saved)
        # Act
        result = Reservation.list()
        # Assert
        assert result == []

    def test_list_returns_all_saved_reservations(self, lease_dir):
        # Arrange
        Reservation(id="spartan-a", name="a", host="spartan", job_id="1").save()
        Reservation(id="spartan-b", name="b", host="spartan", job_id="2").save()
        # Act
        ids = sorted(r.id for r in Reservation.list())
        # Assert
        assert ids == ["spartan-a", "spartan-b"]

    def test_list_skips_corrupt_json_files(self, lease_dir):
        # Arrange
        lease_dir.mkdir(parents=True, exist_ok=True)
        (lease_dir / "broken.json").write_text("not-json")
        Reservation(id="spartan-ok", name="ok", host="spartan", job_id="1").save()
        # Act
        ids = [r.id for r in Reservation.list()]
        # Assert
        assert ids == ["spartan-ok"]


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


class TestBook:
    def test_book_returns_reservation_with_expected_id(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan", cpus=4, time="1-0")
        runner = FakeRunner(
            scripted=[
                _proc(stdout="Submitted batch job 24393466\n"),
                _proc(stdout="RUNNING spartan-bm022.hpc\n"),
            ]
        )
        # Act
        res = _book_with(cfg, runner, persistent=True)
        # Assert
        assert res.id == "spartan-dev-pool"

    def test_book_returns_reservation_with_parsed_job_id(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan", cpus=4, time="1-0")
        runner = FakeRunner(
            scripted=[
                _proc(stdout="Submitted batch job 24393466\n"),
                _proc(stdout="RUNNING spartan-bm022.hpc\n"),
            ]
        )
        # Act
        res = _book_with(cfg, runner, persistent=True)
        # Assert
        assert res.job_id == "24393466"

    def test_book_populates_node_from_squeue_probe(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan", cpus=4, time="1-0")
        runner = FakeRunner(
            scripted=[
                _proc(stdout="Submitted batch job 24393466\n"),
                _proc(stdout="RUNNING spartan-bm022.hpc\n"),
            ]
        )
        # Act
        res = _book_with(cfg, runner, persistent=True)
        # Assert
        assert res.node == "spartan-bm022.hpc"

    def test_book_records_persistent_flag(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan", cpus=4, time="1-0")
        runner = FakeRunner(
            scripted=[
                _proc(stdout="Submitted batch job 24393466\n"),
                _proc(stdout="RUNNING spartan-bm022.hpc\n"),
            ]
        )
        # Act
        res = _book_with(cfg, runner, persistent=True)
        # Assert
        assert res.persistent is True

    def test_book_writes_state_file_with_correct_job_id(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan", cpus=4, time="1-0")
        runner = FakeRunner(
            scripted=[
                _proc(stdout="Submitted batch job 24393466\n"),
                _proc(stdout="RUNNING spartan-bm022.hpc\n"),
            ]
        )
        # Act
        _book_with(cfg, runner)
        # Assert
        on_disk = json.loads((lease_dir / "spartan-dev-pool.json").read_text())
        assert on_disk["job_id"] == "24393466"

    def test_book_writes_state_file_with_correct_node(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan", cpus=4, time="1-0")
        runner = FakeRunner(
            scripted=[
                _proc(stdout="Submitted batch job 24393466\n"),
                _proc(stdout="RUNNING spartan-bm022.hpc\n"),
            ]
        )
        # Act
        _book_with(cfg, runner)
        # Assert
        on_disk = json.loads((lease_dir / "spartan-dev-pool.json").read_text())
        assert on_disk["node"] == "spartan-bm022.hpc"

    def test_book_wraps_sbatch_in_login_shell_bash_lc(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner)
        # Assert
        assert "bash -lc" in runner.commands[0]

    def test_book_raises_runtime_error_on_sbatch_failure(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")
        runner = FakeRunner(scripted=[_proc(returncode=1, stderr="bad partition")])
        # Act
        # Assert
        with pytest.raises(RuntimeError, match="sbatch failed"):
            _book_with(cfg, runner)

    def test_book_raises_when_jobid_unparseable(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")
        runner = FakeRunner(scripted=[_proc(stdout="weird output")])
        # Act
        # Assert
        with pytest.raises(RuntimeError, match="could not parse jobid"):
            _book_with(cfg, runner)

    def test_book_refuses_duplicate_lease_id(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")
        Reservation(
            id="spartan-dev-pool",
            name="dev-pool",
            host="spartan",
            job_id="999",
        ).save()
        runner = FakeRunner()
        # Act
        # Assert
        with pytest.raises(FileExistsError, match="already exists"):
            _book_with(cfg, runner)

    def test_book_requires_non_empty_host(self, lease_dir):
        # Arrange — empty host with isolated SCITEX_DIR (fixture pops user config).
        cfg = JobConfig(project="x", host="")
        runner = FakeRunner()
        # Act
        # Assert
        with pytest.raises(ValueError, match="host is required"):
            _book_with(cfg, runner)

    def test_book_keeps_queued_job_when_poll_times_out(self, lease_dir):
        # Arrange — squeue always returns PENDING; book must NOT scancel.
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 9999\n")
            if "squeue" in command:
                return _proc(stdout="PENDING \n")
            return _proc()

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        res = Reservation.book(
            cfg,
            runner=runner,
            sleep=_noop_sleep,
            monotonic=_clock([0.0, 0.5, 1.5, 2.5, 3.5]),
            poll_timeout=2.0,
            poll_interval=0.1,
        )
        # Assert
        assert res.job_id == "9999"

    def test_book_does_not_scancel_on_poll_timeout(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 9999\n")
            return _proc(stdout="PENDING \n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        Reservation.book(
            cfg,
            runner=runner,
            sleep=_noop_sleep,
            monotonic=_clock([0.0, 0.5, 1.5, 2.5, 3.5]),
            poll_timeout=2.0,
            poll_interval=0.1,
        )
        # Assert
        assert not any("scancel" in c for c in runner.commands)

    def test_book_saves_lease_file_even_on_poll_timeout(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 9999\n")
            return _proc(stdout="PENDING \n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        Reservation.book(
            cfg,
            runner=runner,
            sleep=_noop_sleep,
            monotonic=_clock([0.0, 0.5, 1.5, 2.5, 3.5]),
            poll_timeout=2.0,
            poll_interval=0.1,
        )
        # Assert
        assert (lease_dir / "spartan-dev-pool.json").exists()

    def test_book_returns_reservation_with_empty_node_on_poll_timeout(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="PENDING \n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        res = Reservation.book(
            cfg,
            runner=runner,
            sleep=_noop_sleep,
            monotonic=_clock([0.0, 0.5, 1.5, 2.0, 3.0, 4.0, 5.0]),
            poll_timeout=2.0,
            poll_interval=0.1,
        )
        # Assert
        assert res.node in (None, "")

    def test_book_passes_custom_hold_body_into_sbatch_script(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner, hold_body="echo bootstrapped && tail -f /dev/null")
        # Assert
        assert "echo bootstrapped" in runner.commands[0]

    def test_book_raises_when_job_ends_during_wait(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")
        runner = FakeRunner(
            scripted=[
                _proc(stdout="Submitted batch job 7\n"),
                _proc(stdout="FAILED \n"),
            ]
        )
        # Act
        # Assert
        with pytest.raises(RuntimeError, match="ended in state FAILED"):
            _book_with(cfg, runner)


# ---------------------------------------------------------------------------
# Phase 2 — walltime auto-resubmit
# ---------------------------------------------------------------------------


class TestPersistentBook:
    def test_persistent_adds_signal_directive_to_sbatch(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner, persistent=True)
        # Assert
        assert "--signal=B:USR1@3600" in runner.commands[0]

    def test_persistent_injects_usr1_trap_keyword_into_body(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(
            cfg, runner, persistent=True, hold_body="echo hi && tail -f /dev/null"
        )
        # Assert
        assert "trap _scitex_hpc_walltime_resubmit USR1" in runner.commands[0]

    def test_persistent_invokes_sbatch_with_self_in_trap(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(
            cfg, runner, persistent=True, hold_body="echo hi && tail -f /dev/null"
        )
        # Assert
        assert 'sbatch "$0"' in runner.commands[0]

    def test_persistent_preserves_original_hold_body(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(
            cfg, runner, persistent=True, hold_body="echo hi && tail -f /dev/null"
        )
        # Assert
        assert "echo hi" in runner.commands[0]

    def test_non_persistent_omits_signal_directive(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner, persistent=False)
        # Assert
        assert "--signal=" not in runner.commands[0]

    def test_non_persistent_omits_usr1_trap(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner, persistent=False)
        # Assert
        assert "trap _scitex_hpc_walltime_resubmit" not in runner.commands[0]

    def test_persistent_flag_persists_to_state_file_after_book(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        res = _book_with(cfg, runner, persistent=True)
        loaded = Reservation.get(res.id)
        # Assert
        assert loaded.persistent is True


class TestRefresh:
    def test_refresh_picks_newest_jobid_during_resubmit_overlap(self, lease_dir):
        # Arrange
        res = Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="100",
            node="bm022",
            persistent=True,
        )
        res.save()
        runner = FakeRunner(
            default=_proc(stdout="100 COMPLETING bm022\n101 RUNNING bm175\n")
        )
        res.with_collaborators(runner=runner)
        # Act
        out = res.refresh()
        # Assert
        assert out.job_id == "101"

    def test_refresh_updates_node_to_match_newest_jobid(self, lease_dir):
        # Arrange
        res = Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="100",
            node="bm022",
            persistent=True,
        )
        res.save()
        runner = FakeRunner(
            default=_proc(stdout="100 COMPLETING bm022\n101 RUNNING bm175\n")
        )
        res.with_collaborators(runner=runner)
        # Act
        out = res.refresh()
        # Assert
        assert out.node == "bm175"

    def test_refresh_persists_new_jobid_to_state_file(self, lease_dir):
        # Arrange
        res = Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="100",
            persistent=True,
        )
        res.save()
        runner = FakeRunner(
            default=_proc(stdout="100 COMPLETING bm022\n101 RUNNING bm175\n")
        )
        res.with_collaborators(runner=runner)
        # Act
        res.refresh()
        loaded = Reservation.get("spartan-foo")
        # Assert
        assert loaded.job_id == "101"

    def test_refresh_clears_jobid_when_no_longer_in_queue(self, lease_dir):
        # Arrange
        res = Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            node="bm022",
        )
        res.save()
        runner = FakeRunner(default=_proc(stdout=""))
        res.with_collaborators(runner=runner)
        # Act
        out = res.refresh()
        # Assert
        assert out.job_id == ""

    def test_refresh_clears_node_when_no_longer_in_queue(self, lease_dir):
        # Arrange
        res = Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            node="bm022",
        )
        res.save()
        runner = FakeRunner(default=_proc(stdout=""))
        res.with_collaborators(runner=runner)
        # Act
        out = res.refresh()
        # Assert
        assert out.node == ""

    def test_refresh_filters_squeue_by_friendly_name(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        runner = FakeRunner(default=_proc(stdout="42 RUNNING bm022\n"))
        res.with_collaborators(runner=runner)
        # Act
        res.refresh()
        # Assert — friendly name flows into the squeue --name arg
        assert "--name=" in runner.commands[0] and "foo" in runner.commands[0]

    def test_refresh_query_does_not_filter_by_jobid(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        runner = FakeRunner(default=_proc(stdout="42 RUNNING bm022\n"))
        res.with_collaborators(runner=runner)
        # Act
        res.refresh()
        # Assert
        assert "--jobs=" not in runner.commands[0]

    def test_refresh_query_uses_user_filter(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        runner = FakeRunner(default=_proc(stdout="42 RUNNING bm022\n"))
        res.with_collaborators(runner=runner)
        # Act
        res.refresh()
        # Assert
        assert "--user=$USER" in runner.commands[0]

    def test_refresh_save_false_updates_in_memory_only(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="100")
        res.save()
        runner = FakeRunner(default=_proc(stdout="200 RUNNING bm022\n"))
        res.with_collaborators(runner=runner)
        # Act
        res.refresh(save=False)
        # Assert
        assert res.job_id == "200"

    def test_refresh_save_false_does_not_overwrite_disk(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="100")
        res.save()
        runner = FakeRunner(default=_proc(stdout="200 RUNNING bm022\n"))
        res.with_collaborators(runner=runner)
        # Act
        res.refresh(save=False)
        loaded = Reservation.get("spartan-foo")
        # Assert
        assert loaded.job_id == "100"

    def test_refresh_skips_malformed_squeue_lines(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        runner = FakeRunner(default=_proc(stdout="garbage line\n42 RUNNING bm022\n"))
        res.with_collaborators(runner=runner)
        # Act
        out = res.refresh()
        # Assert
        assert out.job_id == "42"


class TestWrapResubmitTrap:
    def test_trap_function_uses_unique_name(self):
        # Arrange
        body = "echo hi"
        # Act
        wrapped = resmod._wrap_with_resubmit_trap(body)
        # Assert
        assert "_scitex_hpc_walltime_resubmit" in wrapped

    def test_trap_is_registered_on_usr1_signal(self):
        # Arrange
        body = "echo hi"
        # Act
        wrapped = resmod._wrap_with_resubmit_trap(body)
        # Assert
        assert "trap _scitex_hpc_walltime_resubmit USR1" in wrapped

    def test_trap_invokes_sbatch_with_script_self(self):
        # Arrange
        body = "echo hi"
        # Act
        wrapped = resmod._wrap_with_resubmit_trap(body)
        # Assert
        assert 'sbatch "$0"' in wrapped

    def test_wrapper_preserves_first_body_command(self):
        # Arrange
        body = "do_setup\nclaude --skip\n"
        # Act
        wrapped = resmod._wrap_with_resubmit_trap(body)
        # Assert
        assert "do_setup" in wrapped

    def test_wrapper_preserves_second_body_command(self):
        # Arrange
        body = "do_setup\nclaude --skip\n"
        # Act
        wrapped = resmod._wrap_with_resubmit_trap(body)
        # Assert
        assert "claude --skip" in wrapped

    def test_wrapper_installs_trap_before_body(self):
        # Arrange
        body = "do_setup\nclaude --skip\n"
        # Act
        wrapped = resmod._wrap_with_resubmit_trap(body)
        # Assert
        assert wrapped.index("trap _scitex_hpc_walltime_resubmit USR1") < wrapped.index(
            "do_setup"
        )


# ---------------------------------------------------------------------------
# Exec
# ---------------------------------------------------------------------------


class TestExec:
    def test_exec_returns_stdout_from_remote_command(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout="spartan-bm022.hpc\n"))
        res = _running_reservation(runner)
        # Act
        out = res.exec("hostname")
        # Assert
        assert out.stdout.startswith("spartan-bm022")

    def test_exec_invokes_ssh_as_first_arg(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        res = _running_reservation(runner)
        # Act
        res.exec("hostname")
        # Assert — runner is invoked with (host, command); the underlying
        # ssh wrapping happens inside scitex_ssh; we assert on the host.
        assert runner.calls[0][0] == "spartan"

    def test_exec_wraps_remote_command_in_login_shell(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        res = _running_reservation(runner)
        # Act
        res.exec("hostname")
        # Assert
        assert "bash -lc" in runner.commands[0]

    def test_exec_uses_srun_overlap_with_lease_jobid(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        res = _running_reservation(runner)
        # Act
        res.exec("hostname")
        # Assert
        assert "srun --jobid=42 --overlap" in runner.commands[0]

    def test_exec_embeds_command_string_into_remote(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        res = _running_reservation(runner)
        # Act
        res.exec("hostname")
        # Assert
        assert "hostname" in runner.commands[0]

    def test_exec_quotes_first_token_of_list_argv(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc())
        res = _running_reservation(runner)
        # Act
        res.exec(["python", "-c", "print('hi')"])
        # Assert
        assert "'python'" in runner.commands[0]

    def test_exec_quotes_inner_argv_tokens(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc())
        res = _running_reservation(runner)
        # Act
        res.exec(["python", "-c", "print('hi')"])
        # Assert
        assert "'print" in runner.commands[0]

    def test_exec_returns_completedprocess_returncode(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(returncode=7, stdout="x", stderr="y"))
        res = _running_reservation(runner)
        # Act
        out = res.exec("false")
        # Assert
        assert out.returncode == 7

    def test_exec_returns_completedprocess_stdout(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(returncode=7, stdout="x", stderr="y"))
        res = _running_reservation(runner)
        # Act
        out = res.exec("false")
        # Assert
        assert out.stdout == "x"

    def test_exec_returns_completedprocess_stderr(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(returncode=7, stdout="x", stderr="y"))
        res = _running_reservation(runner)
        # Act
        out = res.exec("false")
        # Assert
        assert out.stderr == "y"


# ---------------------------------------------------------------------------
# Attach
# ---------------------------------------------------------------------------


class TestAttach:
    def test_attach_returns_zero_when_remote_exits_clean(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        attach_runner = FakeAttachRunner(returncode=0)
        res.with_collaborators(attach_runner=attach_runner)
        # Act
        rc = res.attach(cmd="bash")
        # Assert
        assert rc == 0

    def test_attach_invokes_ssh_with_first_arg(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        attach_runner = FakeAttachRunner()
        res.with_collaborators(attach_runner=attach_runner)
        # Act
        res.attach(cmd="bash")
        # Assert
        assert attach_runner.calls[0][0] == "ssh"

    def test_attach_passes_dash_t_for_pty(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        attach_runner = FakeAttachRunner()
        res.with_collaborators(attach_runner=attach_runner)
        # Act
        res.attach(cmd="bash")
        # Assert
        assert "-t" in attach_runner.calls[0]

    def test_attach_includes_srun_pty_flag_in_remote_command(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        attach_runner = FakeAttachRunner()
        res.with_collaborators(attach_runner=attach_runner)
        # Act
        res.attach(cmd="bash")
        # Assert
        assert "--pty" in attach_runner.calls[0][-1]


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


class TestRelease:
    def test_release_returns_true_when_scancel_succeeds(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        runner = FakeRunner(default=_proc(returncode=0))
        res.with_collaborators(runner=runner, sleep=_noop_sleep)
        # Act
        ok = res.release()
        # Assert
        assert ok is True

    def test_release_issues_scancel_with_jobid(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        runner = FakeRunner(default=_proc(returncode=0))
        res.with_collaborators(runner=runner, sleep=_noop_sleep)
        # Act
        res.release()
        # Assert
        assert "scancel 42" in runner.commands[0]

    def test_release_removes_state_file_after_scancel(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        runner = FakeRunner(default=_proc(returncode=0))
        res.with_collaborators(runner=runner, sleep=_noop_sleep)
        # Act
        res.release()
        # Assert
        assert not res.state_path.exists()

    def test_release_idempotent_when_state_file_absent(self, lease_dir):
        # Arrange — don't save the lease; state file doesn't exist.
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        runner = FakeRunner(default=_proc(returncode=0))
        res.with_collaborators(runner=runner, sleep=_noop_sleep)
        # Act
        ok = res.release()
        # Assert — completes without raising and reports scancel success.
        assert ok is True

    def test_release_missing_ok_false_raises_on_scancel_failure(self, lease_dir):
        # Arrange
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        runner = FakeRunner(default=_proc(returncode=1, stderr="invalid jobid"))
        res.with_collaborators(runner=runner, sleep=_noop_sleep)
        # Act
        # Assert
        with pytest.raises(RuntimeError, match="scancel"):
            res.release(missing_ok=False)


# ---------------------------------------------------------------------------
# Lease id formatting
# ---------------------------------------------------------------------------


class TestLeaseId:
    def test_lease_id_combines_host_and_name_with_hyphen(self):
        # Arrange
        host, name = "spartan", "dev-pool"
        # Act
        out = resmod._make_lease_id(host, name)
        # Assert
        assert out == "spartan-dev-pool"

    def test_lease_id_sanitizes_slashes_and_spaces(self):
        # Arrange
        host, name = "spartan", "a/b c"
        # Act
        out = resmod._make_lease_id(host, name)
        # Assert
        assert out == "spartan-a-b-c"

    def test_lease_id_preserves_safe_chars(self):
        # Arrange
        host, name = "spartan", "dev_pool.v2-3"
        # Act
        out = resmod._make_lease_id(host, name)
        # Assert
        assert out == "spartan-dev_pool.v2-3"


# ---------------------------------------------------------------------------
# Phase 3 enabler — Reservation.from_jobid
# ---------------------------------------------------------------------------


class TestFromJobid:
    def test_from_jobid_no_refresh_returns_expected_lease_id(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        # Act
        res = Reservation.from_jobid(
            host="spartan",
            job_id="42",
            name="my-pool",
            refresh_node=False,
            runner=runner,
        )
        # Assert
        assert res.id == "spartan-my-pool"

    def test_from_jobid_no_refresh_records_job_id(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        # Act
        res = Reservation.from_jobid(
            host="spartan",
            job_id="42",
            name="my-pool",
            refresh_node=False,
            runner=runner,
        )
        # Assert
        assert res.job_id == "42"

    def test_from_jobid_no_refresh_records_host(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        # Act
        res = Reservation.from_jobid(
            host="spartan",
            job_id="42",
            name="my-pool",
            refresh_node=False,
            runner=runner,
        )
        # Assert
        assert res.host == "spartan"

    def test_from_jobid_no_refresh_leaves_node_empty(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        # Act
        res = Reservation.from_jobid(
            host="spartan",
            job_id="42",
            name="my-pool",
            refresh_node=False,
            runner=runner,
        )
        # Assert
        assert res.node == ""

    def test_from_jobid_no_refresh_skips_squeue_probe(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        # Act
        Reservation.from_jobid(
            host="spartan",
            job_id="42",
            name="my-pool",
            refresh_node=False,
            runner=runner,
        )
        # Assert
        assert runner.calls == []

    def test_from_jobid_default_refresh_populates_node(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout="RUNNING bm022\n"))
        # Act
        res = Reservation.from_jobid(
            host="spartan",
            job_id="42",
            name="my-pool",
            runner=runner,
        )
        # Assert
        assert res.node == "bm022"

    def test_from_jobid_writes_lease_to_state_file(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        # Act
        Reservation.from_jobid(
            host="spartan",
            job_id="42",
            name="my-pool",
            refresh_node=False,
            runner=runner,
        )
        # Assert
        on_disk = (lease_dir / "spartan-my-pool.json").read_text()
        assert '"job_id": "42"' in on_disk

    def test_from_jobid_save_false_skips_disk_write(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        # Act
        Reservation.from_jobid(
            host="spartan",
            job_id="42",
            name="my-pool",
            refresh_node=False,
            save=False,
            runner=runner,
        )
        # Assert
        assert not (lease_dir / "spartan-my-pool.json").exists()

    def test_from_jobid_refuses_overwrite_of_existing_lease(self, lease_dir):
        # Arrange
        runner = FakeRunner(default=_proc(stdout=""))
        Reservation.from_jobid(
            host="spartan",
            job_id="42",
            name="foo",
            refresh_node=False,
            runner=runner,
        )
        # Act
        # Assert
        with pytest.raises(FileExistsError, match="already exists"):
            Reservation.from_jobid(
                host="spartan",
                job_id="99",
                name="foo",
                refresh_node=False,
                runner=runner,
            )

    def test_from_jobid_raises_when_host_is_empty(self, lease_dir):
        # Arrange
        # (empty host triggers validation)
        # Act
        # Assert
        with pytest.raises(ValueError, match="host"):
            Reservation.from_jobid(host="", job_id="1", name="x")

    def test_from_jobid_raises_when_jobid_is_empty(self, lease_dir):
        # Arrange
        # (empty job_id triggers validation)
        # Act
        # Assert
        with pytest.raises(ValueError, match="job_id"):
            Reservation.from_jobid(host="spartan", job_id="", name="x")

    def test_from_jobid_raises_when_name_is_empty(self, lease_dir):
        # Arrange
        # (empty name triggers validation)
        # Act
        # Assert
        with pytest.raises(ValueError, match="name"):
            Reservation.from_jobid(host="spartan", job_id="1", name="")


# ---------------------------------------------------------------------------
# Regression — chatty login-shell banners (Spartan 2026-04-28 incident)
# ---------------------------------------------------------------------------


class TestSqueueParserNoiseTolerance:
    """``_parse_squeue_state_node`` must skip banner noise from chatty
    login shells (.bashrc emits DISPLAY:, XAUTHORITY:, etc. on Spartan)."""

    def test_parses_clean_running_line(self):
        # Arrange
        stdout = "RUNNING node-x\n"
        # Act
        out = resmod._parse_squeue_state_node(stdout)
        # Assert
        assert out == ("RUNNING", "node-x")

    def test_skips_xauthority_and_display_banner_lines(self):
        # Arrange
        spartan_output = (
            "XAUTHORITY: \nXAUTHORITY_LOGIN: \nXAUTHORITY_GPU: \n\n"
            "DISPLAY: 115.146.82.100:15.0\nDISPLAY_LOGIN: 115.146.82.100:15.0\n"
            "DISPLAY_GPU: :42\nRUNNING spartan-bm023\n"
        )
        # Act
        out = resmod._parse_squeue_state_node(spartan_output)
        # Assert
        assert out == ("RUNNING", "spartan-bm023")

    def test_returns_empty_when_no_state_line_present(self):
        # Arrange
        stdout = "DISPLAY: x\nXAUTHORITY:\n"
        # Act
        out = resmod._parse_squeue_state_node(stdout)
        # Assert
        assert out == ("", "")

    def test_returns_empty_on_empty_string_input(self):
        # Arrange
        stdout = ""
        # Act
        out = resmod._parse_squeue_state_node(stdout)
        # Assert
        assert out == ("", "")

    def test_handles_pending_state_with_reason_token(self):
        # Arrange
        stdout = "PENDING (Resources)\n"
        # Act
        out = resmod._parse_squeue_state_node(stdout)
        # Assert
        assert out == ("PENDING", "(Resources)")

    def test_handles_completing_state_during_resubmit_overlap(self):
        # Arrange
        stdout = "DISPLAY: x:0\nCOMPLETING spartan-bm022\n"
        # Act
        out = resmod._parse_squeue_state_node(stdout)
        # Assert
        assert out == ("COMPLETING", "spartan-bm022")

    def test_picks_first_matching_state_when_multiple_present(self):
        # Arrange
        stdout = "RUNNING node-a\nCOMPLETED node-b\n"
        # Act
        out = resmod._parse_squeue_state_node(stdout)
        # Assert
        assert out == ("RUNNING", "node-a")

    def test_squeue_state_method_returns_filtered_running(self, lease_dir):
        # Arrange
        spartan_like = "XAUTHORITY: \nDISPLAY: 1.2.3.4:0\nRUNNING spartan-bm023\n"
        runner = FakeRunner(default=_proc(stdout=spartan_like))
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.with_collaborators(runner=runner)
        # Act
        state, _node = res._squeue_state()
        # Assert
        assert state == "RUNNING"

    def test_squeue_state_method_returns_filtered_node(self, lease_dir):
        # Arrange
        spartan_like = "XAUTHORITY: \nDISPLAY: 1.2.3.4:0\nRUNNING spartan-bm023\n"
        runner = FakeRunner(default=_proc(stdout=spartan_like))
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.with_collaborators(runner=runner)
        # Act
        _state, node = res._squeue_state()
        # Assert
        assert node == "spartan-bm023"


# ---------------------------------------------------------------------------
# Phase 4 enabler — tmux server bootstrap
# ---------------------------------------------------------------------------


class TestTmuxServerBootstrap:
    """``tmux_server`` makes the sbatch job run a long-lived tmux server
    as PID 1, so tenants attaching via ``srun --jobid --overlap`` don't
    get cgroup-killed when their step ends."""

    def test_bootstrap_fragment_invokes_tmux_with_named_socket(self):
        # Arrange
        socket = "sac"
        # Act
        body = resmod._tmux_server_bootstrap(socket)
        # Assert
        assert "tmux -L sac new-session -d -s _root" in body

    def test_bootstrap_fragment_uses_sleep_infinity_as_root_session(self):
        # Arrange
        socket = "sac"
        # Act
        body = resmod._tmux_server_bootstrap(socket)
        # Assert
        assert "sleep infinity" in body

    def test_bootstrap_fragment_sanitizes_unsafe_socket_name(self):
        # Arrange
        socket = "a/b c"
        # Act
        body = resmod._tmux_server_bootstrap(socket)
        # Assert
        assert "tmux -L a-b-c new-session" in body

    def test_bootstrap_fragment_is_idempotent_via_or_true(self):
        # Arrange
        socket = "sac"
        # Act
        body = resmod._tmux_server_bootstrap(socket)
        # Assert
        assert "|| true" in body

    def test_book_with_tmux_server_includes_bootstrap_in_sbatch_script(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner, tmux_server="sac")
        # Assert
        assert "tmux -L sac new-session" in runner.commands[0]

    def test_book_with_tmux_server_uses_backgrounded_sleep_infinity(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner, tmux_server="sac")
        # Assert
        assert "sleep infinity &" in runner.commands[0]

    def test_book_with_tmux_server_uses_wait_for_signal_delivery(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner, tmux_server="sac")
        # Assert
        assert "wait $!" in runner.commands[0]

    def test_book_records_socket_name_into_extras(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        res = _book_with(cfg, runner, tmux_server="sac")
        # Assert
        assert res.extras.get("tmux_server") == "sac"

    def test_book_round_trips_socket_name_through_state_file(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        res = _book_with(cfg, runner, tmux_server="sac")
        loaded = Reservation.get(res.id)
        # Assert
        assert loaded.extras.get("tmux_server") == "sac"

    def test_book_without_tmux_server_does_not_invoke_tmux(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner)
        # Assert
        assert "tmux -L" not in runner.commands[0]

    def test_book_without_tmux_server_does_not_create_session(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner)
        # Assert
        assert "new-session" not in runner.commands[0]

    def test_book_combines_persistent_trap_with_tmux_bootstrap(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner, persistent=True, tmux_server="sac")
        # Assert
        assert "trap _scitex_hpc_walltime_resubmit USR1" in runner.commands[0]

    def test_book_combines_persistent_tmux_with_tmux_bootstrap_invocation(
        self, lease_dir
    ):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner, persistent=True, tmux_server="sac")
        # Assert
        assert "tmux -L sac new-session" in runner.commands[0]

    def test_book_combines_persistent_tmux_with_signal_directive(self, lease_dir):
        # Arrange
        cfg = JobConfig(project="dev-pool", host="spartan")

        def dispatch(host, command):
            if "sbatch" in command:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        runner = FakeRunner(dispatcher=dispatch)
        # Act
        _book_with(cfg, runner, persistent=True, tmux_server="sac")
        # Assert
        assert "--signal=B:USR1@3600" in runner.commands[0]
