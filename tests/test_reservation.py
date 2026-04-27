"""Tests for scitex_hpc.Reservation (mocked subprocess + tmp lease dir)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scitex_hpc import JobConfig, Reservation
from scitex_hpc import _reservation as resmod

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lease_dir(tmp_path, monkeypatch) -> Path:
    """Isolate the lease directory per test."""
    d = tmp_path / "leases"
    monkeypatch.setenv("SCITEX_HPC_LEASE_DIR", str(d))
    return d


def _proc(*, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _scripted_run(steps):
    """Return a fake subprocess.run that returns each item in steps in order.

    ``steps`` is a list of CompletedProcess instances. Calls beyond the
    list raise so unintended subprocess calls fail loudly.
    """
    it = iter(steps)

    def _run(*args, **kwargs):
        try:
            return next(it)
        except StopIteration as e:  # pragma: no cover — surfaces test bug
            raise AssertionError(f"unexpected subprocess.run call: {args}") from e

    return _run


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_and_load_round_trip(self, lease_dir):
        res = Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            node="spartan-bm022.hpc",
            persistent=True,
        )
        res.save()
        assert (lease_dir / "spartan-foo.json").is_file()
        loaded = Reservation.get("spartan-foo")
        assert loaded is not None
        assert loaded.job_id == "42"
        assert loaded.persistent is True

    def test_get_by_friendly_name(self, lease_dir):
        Reservation(id="spartan-foo", name="foo", host="spartan", job_id="1").save()
        Reservation(id="cedar-foo", name="foo", host="cedar", job_id="2").save()
        # Ambiguous name returns first match (sorted)
        loaded = Reservation.get("foo")
        assert loaded is not None
        # With host filter, picks the right one
        assert Reservation.get("foo", host="spartan").job_id == "1"
        assert Reservation.get("foo", host="cedar").job_id == "2"

    def test_get_missing_returns_none(self, lease_dir):
        assert Reservation.get("nope") is None

    def test_require_raises_on_missing(self, lease_dir):
        with pytest.raises(KeyError, match="no reservation"):
            Reservation.require("nope")

    def test_list_empty(self, lease_dir):
        assert Reservation.list() == []

    def test_list_returns_all(self, lease_dir):
        Reservation(id="spartan-a", name="a", host="spartan", job_id="1").save()
        Reservation(id="spartan-b", name="b", host="spartan", job_id="2").save()
        ids = sorted(r.id for r in Reservation.list())
        assert ids == ["spartan-a", "spartan-b"]

    def test_list_skips_corrupt_files(self, lease_dir):
        lease_dir.mkdir(parents=True, exist_ok=True)
        (lease_dir / "broken.json").write_text("not-json")
        # An otherwise valid lease still loads
        Reservation(id="spartan-ok", name="ok", host="spartan", job_id="1").save()
        ids = [r.id for r in Reservation.list()]
        assert ids == ["spartan-ok"]


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


class TestBook:
    def test_book_submits_sbatch_and_waits_for_allocation(self, lease_dir, monkeypatch):
        cfg = JobConfig(project="dev-pool", host="spartan", cpus=4, time="1-0")
        run = _scripted_run(
            [
                _proc(stdout="Submitted batch job 24393466\n"),  # sbatch
                _proc(stdout="RUNNING spartan-bm022.hpc\n"),  # squeue probe
            ]
        )
        monkeypatch.setattr(resmod.subprocess, "run", run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)

        res = Reservation.book(cfg, persistent=True)

        assert res.id == "spartan-dev-pool"
        assert res.job_id == "24393466"
        assert res.node == "spartan-bm022.hpc"
        assert res.persistent is True
        # State file exists with the right content
        on_disk = json.loads((lease_dir / "spartan-dev-pool.json").read_text())
        assert on_disk["job_id"] == "24393466"
        assert on_disk["node"] == "spartan-bm022.hpc"

    def test_book_uses_login_shell_wrapper(self, lease_dir, monkeypatch):
        """sbatch must run under bash -lc so SLURM is on PATH."""
        cfg = JobConfig(project="dev-pool", host="spartan")
        captured = []

        def fake_run(*args, **kwargs):
            captured.append(args[0])
            if "sbatch" in args[0][2]:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)

        Reservation.book(cfg)
        # First call is sbatch, must be wrapped in bash -lc
        assert captured[0][0] == "ssh"
        assert "bash -lc" in captured[0][2]

    def test_book_raises_on_sbatch_failure(self, lease_dir, monkeypatch):
        cfg = JobConfig(project="dev-pool", host="spartan")
        monkeypatch.setattr(
            resmod.subprocess,
            "run",
            _scripted_run([_proc(returncode=1, stderr="bad partition")]),
        )
        with pytest.raises(RuntimeError, match="sbatch failed"):
            Reservation.book(cfg)

    def test_book_raises_when_jobid_unparseable(self, lease_dir, monkeypatch):
        cfg = JobConfig(project="dev-pool", host="spartan")
        monkeypatch.setattr(
            resmod.subprocess,
            "run",
            _scripted_run([_proc(stdout="weird output")]),
        )
        with pytest.raises(RuntimeError, match="could not parse jobid"):
            Reservation.book(cfg)

    def test_book_refuses_duplicate_lease(self, lease_dir, monkeypatch):
        cfg = JobConfig(project="dev-pool", host="spartan")
        Reservation(
            id="spartan-dev-pool", name="dev-pool", host="spartan", job_id="999"
        ).save()
        with pytest.raises(FileExistsError, match="already exists"):
            Reservation.book(cfg)

    def test_book_requires_host(self, lease_dir, monkeypatch):
        # Force resolve('host') to return empty regardless of user config /
        # env, so we exercise the validation path. (Without this monkeypatch
        # a developer with ``host: spartan`` in ~/.scitex/dev/config.yaml
        # would have the JobConfig silently inherit it and book() would
        # try to ssh to a real cluster — that exact scenario submitted a
        # runaway SLURM job on 2026-04-28.)
        monkeypatch.setattr(
            JobConfig,
            "resolve",
            lambda self, key: "" if key == "host" else "x",
        )
        with pytest.raises(ValueError, match="host is required"):
            Reservation.book(JobConfig(project="x"))

    def test_book_cancels_job_when_allocation_times_out(self, lease_dir, monkeypatch):
        """Regression: book() must scancel the submitted job if it never
        reaches RUNNING. The 2026-04-28 incident saw a 5-minute orphan job
        because we previously raised before cleanup."""
        cfg = JobConfig(project="dev-pool", host="spartan")
        captured = []

        def fake_run(*args, **kwargs):
            captured.append(args[0][2])
            cmd = args[0][2]
            if "sbatch" in cmd:
                return _proc(stdout="Submitted batch job 9999\n")
            if "squeue" in cmd:
                return _proc(stdout="PENDING \n")
            if "scancel" in cmd:
                return _proc()
            return _proc()

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)
        clock = iter([0.0, 0.5, 1.5, 2.5, 3.5])
        monkeypatch.setattr(resmod.time, "monotonic", lambda: next(clock))

        with pytest.raises(TimeoutError):
            Reservation.book(cfg, poll_timeout=2.0, poll_interval=0.1)

        # Must have called scancel on the orphan
        assert any("scancel 9999" in c for c in captured), (
            f"expected scancel cleanup; got: {captured}"
        )
        # And no state file was left behind
        assert not (lease_dir / "spartan-dev-pool.json").exists()

    def test_book_uses_custom_hold_body(self, lease_dir, monkeypatch):
        """``hold_body`` lets sac-style consumers inject tmux/claude bootstrap."""
        cfg = JobConfig(project="dev-pool", host="spartan")
        captured = []

        def fake_run(*args, **kwargs):
            captured.append(args[0])
            if "sbatch" in args[0][2]:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)

        Reservation.book(cfg, hold_body="echo bootstrapped && tail -f /dev/null")
        assert "echo bootstrapped" in captured[0][2]

    def test_book_times_out_when_never_allocated(self, lease_dir, monkeypatch):
        cfg = JobConfig(project="dev-pool", host="spartan")

        def fake_run(*args, **kwargs):
            if "sbatch" in args[0][2]:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="PENDING \n")  # never allocates

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)

        # Drive monotonic forward fast so timeout fires
        clock = iter([0.0, 0.5, 1.5, 2.0, 3.0, 4.0, 5.0])
        monkeypatch.setattr(resmod.time, "monotonic", lambda: next(clock))

        with pytest.raises(TimeoutError, match="did not reach RUNNING"):
            Reservation.book(cfg, poll_timeout=2.0, poll_interval=0.1)

    def test_book_raises_if_job_ends_during_wait(self, lease_dir, monkeypatch):
        cfg = JobConfig(project="dev-pool", host="spartan")
        monkeypatch.setattr(
            resmod.subprocess,
            "run",
            _scripted_run(
                [
                    _proc(stdout="Submitted batch job 7\n"),
                    _proc(stdout="FAILED \n"),
                ]
            ),
        )
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)
        with pytest.raises(RuntimeError, match="ended in state FAILED"):
            Reservation.book(cfg)


# ---------------------------------------------------------------------------
# Phase 2 — walltime auto-resubmit
# ---------------------------------------------------------------------------


class TestPersistentBook:
    """Verify ``persistent=True`` injects the SIGUSR1 trap + signal directive."""

    def test_persistent_adds_signal_directive(self, lease_dir, monkeypatch):
        cfg = JobConfig(project="dev-pool", host="spartan")
        captured = []

        def fake_run(*args, **kwargs):
            captured.append(args[0][2])
            cmd = args[0][2]
            if "sbatch" in cmd:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)
        Reservation.book(cfg, persistent=True)

        # The sbatch invocation must include the walltime signal directive
        sbatch_call = captured[0]
        assert "--signal=B:USR1@3600" in sbatch_call

    def test_persistent_injects_usr1_trap_into_body(self, lease_dir, monkeypatch):
        cfg = JobConfig(project="dev-pool", host="spartan")
        captured = []

        def fake_run(*args, **kwargs):
            captured.append(args[0][2])
            cmd = args[0][2]
            if "sbatch" in cmd:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)
        Reservation.book(cfg, persistent=True, hold_body="echo hi && tail -f /dev/null")

        sbatch_call = captured[0]
        # Trap must call sbatch with $0 to resubmit in place
        assert "trap _scitex_hpc_walltime_resubmit USR1" in sbatch_call
        assert 'sbatch "$0"' in sbatch_call
        # Original hold body must still be present after the trap
        assert "echo hi" in sbatch_call

    def test_non_persistent_omits_trap_and_signal(self, lease_dir, monkeypatch):
        cfg = JobConfig(project="dev-pool", host="spartan")
        captured = []

        def fake_run(*args, **kwargs):
            captured.append(args[0][2])
            cmd = args[0][2]
            if "sbatch" in cmd:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)
        Reservation.book(cfg, persistent=False)

        sbatch_call = captured[0]
        assert "--signal=" not in sbatch_call
        assert "trap _scitex_hpc_walltime_resubmit" not in sbatch_call

    def test_persistent_flag_persists_to_state_file(self, lease_dir, monkeypatch):
        cfg = JobConfig(project="dev-pool", host="spartan")

        def fake_run(*args, **kwargs):
            cmd = args[0][2]
            if "sbatch" in cmd:
                return _proc(stdout="Submitted batch job 1\n")
            return _proc(stdout="RUNNING node-x\n")

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)
        res = Reservation.book(cfg, persistent=True)
        loaded = Reservation.get(res.id)
        assert loaded is not None
        assert loaded.persistent is True


class TestRefresh:
    """Verify ``refresh()`` re-discovers job_id after a walltime resubmit."""

    def test_refresh_picks_up_new_jobid_after_resubmit(self, lease_dir, monkeypatch):
        # Original job 100 was submitted; SLURM auto-resubmitted to 101.
        # Squeue --user --name returns both during the overlap window.
        res = Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="100",
            node="bm022",
            persistent=True,
        )
        res.save()

        def fake_run(*args, **kwargs):
            return _proc(stdout="100 COMPLETING bm022\n101 RUNNING bm175\n")

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        out = res.refresh()
        # Newest jobid wins (101 > 100)
        assert out.job_id == "101"
        assert out.node == "bm175"
        # State persisted
        loaded = Reservation.get("spartan-foo")
        assert loaded is not None and loaded.job_id == "101"

    def test_refresh_clears_when_job_no_longer_in_queue(self, lease_dir, monkeypatch):
        res = Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            node="bm022",
        )
        res.save()
        monkeypatch.setattr(resmod.subprocess, "run", lambda *a, **k: _proc(stdout=""))
        out = res.refresh()
        assert out.job_id == ""
        assert out.node == ""

    def test_refresh_uses_friendly_name_in_squeue_query(self, lease_dir, monkeypatch):
        """squeue must filter by --name=<friendly>, not by --jobs=<id>."""
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        captured = []

        def fake_run(*args, **kwargs):
            captured.append(args[0][2])
            return _proc(stdout="42 RUNNING bm022\n")

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        res.refresh()
        cmd = captured[0]
        # The remote command goes through ``bash -lc 'squeue …'`` so the
        # inner ``--name='foo'`` gets the standard POSIX
        # double-escape ('\''foo'\''). Assert the semantics without
        # depending on shell-escape details.
        assert "squeue" in cmd and "--user=$USER" in cmd
        assert "--name=" in cmd and "foo" in cmd
        assert "--jobs=" not in cmd

    def test_refresh_no_save_when_save_false(self, lease_dir, monkeypatch):
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="100")
        res.save()
        monkeypatch.setattr(
            resmod.subprocess,
            "run",
            lambda *a, **k: _proc(stdout="200 RUNNING bm022\n"),
        )
        res.refresh(save=False)
        # In-memory updated
        assert res.job_id == "200"
        # On-disk NOT updated
        loaded = Reservation.get("spartan-foo")
        assert loaded is not None and loaded.job_id == "100"

    def test_refresh_skips_malformed_squeue_lines(self, lease_dir, monkeypatch):
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        # Mix of real rows and noise (e.g. squeue header glitches)
        monkeypatch.setattr(
            resmod.subprocess,
            "run",
            lambda *a, **k: _proc(stdout="garbage line\n42 RUNNING bm022\n"),
        )
        out = res.refresh()
        assert out.job_id == "42"


class TestWrapResubmitTrap:
    def test_trap_function_uniquely_named(self):
        wrapped = resmod._wrap_with_resubmit_trap("echo hi")
        assert "_scitex_hpc_walltime_resubmit" in wrapped
        assert "trap _scitex_hpc_walltime_resubmit USR1" in wrapped

    def test_trap_calls_sbatch_with_self(self):
        wrapped = resmod._wrap_with_resubmit_trap("echo hi")
        assert 'sbatch "$0"' in wrapped

    def test_original_body_preserved(self):
        wrapped = resmod._wrap_with_resubmit_trap("do_setup\nclaude --skip\n")
        # Original commands still reachable
        assert "do_setup" in wrapped
        assert "claude --skip" in wrapped
        # Trap is installed BEFORE the body so the signal is caught
        # while the body runs
        body_idx = wrapped.index("do_setup")
        trap_idx = wrapped.index("trap _scitex_hpc_walltime_resubmit USR1")
        assert trap_idx < body_idx


# ---------------------------------------------------------------------------
# Exec
# ---------------------------------------------------------------------------


class TestExec:
    def _make_running(self, lease_dir):
        res = Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            node="spartan-bm022.hpc",
        )
        res.save()
        return res

    def test_exec_invokes_srun_overlap(self, lease_dir, monkeypatch):
        res = self._make_running(lease_dir)
        captured = []

        def fake_run(*args, **kwargs):
            captured.append(args[0])
            return _proc(stdout="spartan-bm022.hpc\n")

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        out = res.exec("hostname")

        assert out.stdout.startswith("spartan-bm022")
        cmd = captured[0]
        assert cmd[0] == "ssh"
        assert cmd[1] == "spartan"
        assert "bash -lc" in cmd[2]
        assert "srun --jobid=42 --overlap" in cmd[2]
        assert "hostname" in cmd[2]

    def test_exec_accepts_list_argv(self, lease_dir, monkeypatch):
        res = self._make_running(lease_dir)
        captured = []

        def fake_run(*args, **kwargs):
            captured.append(args[0])
            return _proc()

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        res.exec(["python", "-c", "print('hi')"])
        # All argv tokens must be POSIX-quoted into the remote command
        assert "'python'" in captured[0][2]
        assert "'print" in captured[0][2]

    def test_exec_returns_completedprocess(self, lease_dir, monkeypatch):
        res = self._make_running(lease_dir)
        monkeypatch.setattr(
            resmod.subprocess,
            "run",
            lambda *a, **k: _proc(returncode=7, stdout="x", stderr="y"),
        )
        out = res.exec("false")
        assert out.returncode == 7
        assert out.stdout == "x"
        assert out.stderr == "y"


# ---------------------------------------------------------------------------
# Attach
# ---------------------------------------------------------------------------


class TestAttach:
    def test_attach_uses_pty_and_t_flag(self, lease_dir, monkeypatch):
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        captured = []

        def fake_run(*args, **kwargs):
            captured.append(args[0])
            return _proc(returncode=0)

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        rc = res.attach(cmd="bash")
        assert rc == 0
        cmd = captured[0]
        assert cmd[0] == "ssh"
        assert "-t" in cmd
        assert "--pty" in cmd[-1]


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


class TestRelease:
    def test_release_scancels_and_removes_state(self, lease_dir, monkeypatch):
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        assert res.state_path.exists()
        captured = []

        def fake_run(*args, **kwargs):
            captured.append(args[0])
            return _proc(returncode=0)

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)
        ok = res.release()
        assert ok is True
        assert "scancel 42" in captured[0][2]
        assert not res.state_path.exists()

    def test_release_idempotent_when_state_already_gone(self, lease_dir, monkeypatch):
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        # Don't save — state file doesn't exist
        monkeypatch.setattr(
            resmod.subprocess, "run", lambda *a, **k: _proc(returncode=0)
        )
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)
        # Must not raise
        res.release()

    def test_release_missing_ok_false_raises(self, lease_dir, monkeypatch):
        res = Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42")
        res.save()
        monkeypatch.setattr(
            resmod.subprocess,
            "run",
            lambda *a, **k: _proc(returncode=1, stderr="invalid jobid"),
        )
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)
        with pytest.raises(RuntimeError, match="scancel"):
            res.release(missing_ok=False)


# ---------------------------------------------------------------------------
# Lease id formatting
# ---------------------------------------------------------------------------


class TestLeaseId:
    def test_lease_id_combines_host_and_name(self):
        assert resmod._make_lease_id("spartan", "dev-pool") == "spartan-dev-pool"

    def test_lease_id_sanitizes_unsafe_chars(self):
        # Slashes, spaces, etc. become hyphens
        assert resmod._make_lease_id("spartan", "a/b c") == "spartan-a-b-c"

    def test_lease_id_keeps_safe_chars(self):
        assert (
            resmod._make_lease_id("spartan", "dev_pool.v2-3") == "spartan-dev_pool.v2-3"
        )


# ---------------------------------------------------------------------------
# Phase 3 enabler — Reservation.from_jobid
# ---------------------------------------------------------------------------


class TestFromJobid:
    """Adopt an already-submitted SLURM job into a Reservation."""

    def test_from_jobid_creates_lease_with_no_squeue_when_refresh_off(
        self, lease_dir, monkeypatch
    ):
        """``refresh_node=False`` skips the squeue probe."""
        called = []
        monkeypatch.setattr(
            resmod.subprocess, "run",
            lambda *a, **k: called.append(a) or _proc(stdout=""),
        )
        res = Reservation.from_jobid(
            host="spartan", job_id="42", name="my-pool", refresh_node=False
        )
        assert res.id == "spartan-my-pool"
        assert res.job_id == "42"
        assert res.host == "spartan"
        assert res.node == ""
        assert called == []

    def test_from_jobid_refreshes_node_by_default(
        self, lease_dir, monkeypatch
    ):
        monkeypatch.setattr(
            resmod.subprocess, "run",
            lambda *a, **k: _proc(stdout="RUNNING bm022\n"),
        )
        res = Reservation.from_jobid(
            host="spartan", job_id="42", name="my-pool"
        )
        assert res.node == "bm022"

    def test_from_jobid_persists_to_state_file(self, lease_dir, monkeypatch):
        monkeypatch.setattr(
            resmod.subprocess, "run", lambda *a, **k: _proc(stdout="")
        )
        Reservation.from_jobid(
            host="spartan", job_id="42", name="my-pool", refresh_node=False
        )
        on_disk = (lease_dir / "spartan-my-pool.json").read_text()
        assert "\"job_id\": \"42\"" in on_disk

    def test_from_jobid_save_false_skips_disk_write(
        self, lease_dir, monkeypatch
    ):
        monkeypatch.setattr(
            resmod.subprocess, "run", lambda *a, **k: _proc(stdout="")
        )
        Reservation.from_jobid(
            host="spartan", job_id="42", name="my-pool",
            refresh_node=False, save=False,
        )
        assert not (lease_dir / "spartan-my-pool.json").exists()

    def test_from_jobid_refuses_overwrite(self, lease_dir, monkeypatch):
        monkeypatch.setattr(
            resmod.subprocess, "run", lambda *a, **k: _proc(stdout="")
        )
        Reservation.from_jobid(
            host="spartan", job_id="42", name="foo", refresh_node=False
        )
        with pytest.raises(FileExistsError, match="already exists"):
            Reservation.from_jobid(
                host="spartan", job_id="99", name="foo", refresh_node=False
            )

    def test_from_jobid_validates_inputs(self, lease_dir):
        with pytest.raises(ValueError, match="host"):
            Reservation.from_jobid(host="", job_id="1", name="x")
        with pytest.raises(ValueError, match="job_id"):
            Reservation.from_jobid(host="spartan", job_id="", name="x")
        with pytest.raises(ValueError, match="name"):
            Reservation.from_jobid(host="spartan", job_id="1", name="")
