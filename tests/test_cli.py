"""Smoke tests for the scitex-hpc CLI (argparse plumbing + JSON output)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scitex_hpc import Reservation
from scitex_hpc import _reservation as resmod
from scitex_hpc._cli import main


@pytest.fixture
def lease_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "leases"
    monkeypatch.setenv("SCITEX_HPC_LEASE_DIR", str(d))
    return d


def _proc(*, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestList:
    def test_list_empty(self, lease_dir, capsys):
        rc = main(["reservations", "list"])
        assert rc == 0
        assert "(no reservations)" in capsys.readouterr().out

    def test_list_json(self, lease_dir, capsys):
        Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            node="spartan-bm022.hpc",
        ).save()
        rc = main(["reservations", "list", "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out[0]["id"] == "spartan-foo"
        assert out[0]["job_id"] == "42"

    def test_list_table(self, lease_dir, capsys):
        Reservation(
            id="spartan-foo",
            name="foo",
            host="spartan",
            job_id="42",
            node="n1",
            persistent=True,
        ).save()
        main(["reservations", "list"])
        out = capsys.readouterr().out
        assert "spartan-foo" in out
        assert "yes" in out  # persistent column


class TestGet:
    def test_get_missing_returns_2(self, lease_dir):
        rc = main(["reservations", "get", "nope"])
        assert rc == 2

    def test_get_found(self, lease_dir, capsys):
        Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42").save()
        rc = main(["reservations", "get", "spartan-foo"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["job_id"] == "42"


class TestExec:
    def test_exec_propagates_returncode(self, lease_dir, monkeypatch, capsys):
        Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42").save()
        monkeypatch.setattr(
            resmod.subprocess,
            "run",
            lambda *a, **k: _proc(returncode=7, stdout="hi\n", stderr="err\n"),
        )
        rc = main(["reservations", "exec", "spartan-foo", "echo hi"])
        assert rc == 7
        captured = capsys.readouterr()
        assert "hi" in captured.out
        assert "err" in captured.err


class TestRelease:
    def test_release_missing_is_idempotent(self, lease_dir, capsys):
        rc = main(["reservations", "release", "nope"])
        assert rc == 0  # missing_ok default

    def test_release_calls_scancel(self, lease_dir, monkeypatch, capsys):
        Reservation(id="spartan-foo", name="foo", host="spartan", job_id="42").save()
        called = []

        def fake_run(*a, **k):
            called.append(a[0])
            return _proc()

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)
        rc = main(["reservations", "release", "spartan-foo"])
        assert rc == 0
        assert any("scancel 42" in c[2] for c in called)


class TestBookSmoke:
    def test_book_subcommand_invokes_book(self, lease_dir, monkeypatch, capsys):
        def fake_run(*a, **k):
            cmd = a[0][2]
            if "sbatch" in cmd:
                return _proc(stdout="Submitted batch job 99\n")
            return _proc(stdout="RUNNING n1\n")

        monkeypatch.setattr(resmod.subprocess, "run", fake_run)
        monkeypatch.setattr(resmod.time, "sleep", lambda _: None)
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
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["job_id"] == "99"
        assert out["node"] == "n1"


class TestBookTmuxServer:
    """`--tmux-server` flag wires through to Reservation.book(tmux_server=)."""

    def test_book_with_tmux_server_flag(self, lease_dir, monkeypatch, capsys):
        captured: list[dict] = []

        def fake_book(cfg, **kwargs):
            captured.append(kwargs)
            return Reservation(
                id="spartan-test", name="test", host="spartan",
                job_id="42", node="n1", extras=kwargs.get("extras", {}),
            )

        # We mock the Reservation.book classmethod to capture kwargs
        from scitex_hpc._cli import Reservation as CliReservation
        monkeypatch.setattr(CliReservation, "book", fake_book)
        rc = main([
            "reservations", "book", "test",
            "--host", "spartan",
            "--tmux-server", "sac",
        ])
        assert rc == 0
        assert captured[0].get("tmux_server") == "sac"

    def test_book_without_tmux_server_passes_none(
        self, lease_dir, monkeypatch
    ):
        captured: list[dict] = []

        def fake_book(cfg, **kwargs):
            captured.append(kwargs)
            return Reservation(
                id="spartan-test", name="test", host="spartan",
                job_id="42", node="n1",
            )

        from scitex_hpc._cli import Reservation as CliReservation
        monkeypatch.setattr(CliReservation, "book", fake_book)
        main(["reservations", "book", "test", "--host", "spartan"])
        assert captured[0].get("tmux_server") is None
