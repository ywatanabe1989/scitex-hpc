"""Reservation primitive — book an HPC node once, exec many commands inside it.

Motivation: HPC queue wait dominates iteration time. A 30-second test run
waits 5 min in the queue. ``Reservation`` lets you submit ONE long-running
hold-job, then run many short commands inside its allocation via
``srun --jobid=<X> --overlap``. The allocation persists across commands.

Lifecycle:

    res = Reservation.book(JobConfig(host="spartan", time="7-0", ...))
    res.exec("hostname")              # → "spartan-bm022.hpc.unimelb.edu.au\\n"
    res.exec(["python", "-V"])
    res.attach(cmd="bash")            # interactive on compute node
    res.release()                     # scancel + clear lease

State (one JSON file per lease):

    ~/.scitex/hpc/leases/<lease_id>.json

Lease id format: ``<host>-<name>`` — friendly name uniqueness is enforced
per host. (Phase 2 will add walltime auto-resubmit; lease id stays stable
across resubmits even though job_id changes.)

Compatible with the 2026-04-26 IT Security ruling on Spartan: bastion-
initiated SSH only, no daemons or tunnels on the login or compute nodes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._config import JobConfig
from ._dispatch import _quote, _wrap_in_login_shell

_DEFAULT_HOLD_BODY = "tail -f /dev/null"
_LEASE_DIR_ENV = "SCITEX_HPC_LEASE_DIR"
_DEFAULT_POLL_INTERVAL = 2.0
_DEFAULT_POLL_TIMEOUT = 300.0
_DEFAULT_RELEASE_BACKOFF = 0.5


def _lease_dir() -> Path:
    """Return ``~/.scitex/hpc/leases/`` (override via ``SCITEX_HPC_LEASE_DIR``)."""
    override = os.environ.get(_LEASE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".scitex" / "hpc" / "leases"


def _make_lease_id(host: str, name: str) -> str:
    """Lease id is ``<host>-<name>``. Names must be filesystem-safe."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", name)
    return f"{host}-{safe}"


@dataclass
class Reservation:
    """An open SLURM allocation that accepts many commands."""

    id: str
    name: str
    host: str
    job_id: str
    node: str = ""
    submitted_at: str = ""
    walltime_end: str = ""
    persistent: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return _lease_dir() / f"{self.id}.json"

    def save(self) -> None:
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))

    @classmethod
    def _from_path(cls, path: Path) -> "Reservation":
        data = json.loads(path.read_text())
        return cls(**data)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, name_or_id: str, *, host: str | None = None) -> "Reservation | None":
        """Look up a reservation by lease id, or by friendly name (+ optional host)."""
        d = _lease_dir()
        if not d.is_dir():
            return None
        # Direct hit by lease id
        direct = d / f"{name_or_id}.json"
        if direct.is_file():
            return cls._from_path(direct)
        # Search by name; if host given, narrow the search
        for p in sorted(d.glob("*.json")):
            try:
                res = cls._from_path(p)
            except (json.JSONDecodeError, TypeError):
                continue
            if res.name != name_or_id:
                continue
            if host is not None and res.host != host:
                continue
            return res
        return None

    @classmethod
    def require(cls, name_or_id: str, *, host: str | None = None) -> "Reservation":
        """Like ``get`` but raises ``KeyError`` if missing."""
        res = cls.get(name_or_id, host=host)
        if res is None:
            scope = f" on {host}" if host else ""
            raise KeyError(f"no reservation named {name_or_id!r}{scope}")
        return res

    @classmethod
    def list(cls) -> list["Reservation"]:
        """All reservations on the current host's lease dir."""
        d = _lease_dir()
        if not d.is_dir():
            return []
        out: list[Reservation] = []
        for p in sorted(d.glob("*.json")):
            try:
                out.append(cls._from_path(p))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    # ------------------------------------------------------------------
    # Booking
    # ------------------------------------------------------------------

    @classmethod
    def book(
        cls,
        config: JobConfig,
        *,
        persistent: bool = False,
        hold_body: str | None = None,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
    ) -> "Reservation":
        """Submit a hold-job, wait for SLURM to allocate a node, return Reservation.

        ``hold_body`` is the body of the sbatch script. Defaults to
        ``tail -f /dev/null`` so the allocation persists. Pass a custom body
        when a consumer (e.g. scitex-agent-container) needs to set up tmux,
        traps, etc. on the compute node before the hold.

        ``persistent=True`` is a phase-2 marker — it's saved to the state
        file and the auto-resubmit USR1 trap will be added in a follow-up.
        For phase 1, the flag is recorded but does not yet emit the trap.
        """
        host = config.resolve("host")
        if not host:
            raise ValueError("JobConfig.host is required for Reservation.book()")
        name = (config.job_name or config.project or "lease").strip()
        if not name:
            raise ValueError("Reservation.book() requires a non-empty name")
        lease_id = _make_lease_id(host, name)

        # Refuse to overwrite an existing live lease silently
        existing = cls.get(lease_id)
        if existing is not None:
            raise FileExistsError(
                f"reservation {lease_id} already exists; release it first"
            )

        body = hold_body if hold_body is not None else _DEFAULT_HOLD_BODY
        sbatch_args = [*config.slurm_args(), *config.extra_sbatch_args]
        script_body = (
            "#!/bin/bash\n#SBATCH " + "\n#SBATCH ".join(sbatch_args) + f"\n{body}\n"
        )
        inner = f"sbatch <(printf %s {_quote(script_body)})"
        result = subprocess.run(
            ["ssh", host, _wrap_in_login_shell(inner)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"sbatch failed: {result.stderr.strip()}")
        m = re.search(r"Submitted batch job (\d+)", result.stdout)
        if not m:
            raise RuntimeError(
                f"could not parse jobid from sbatch output: {result.stdout!r}"
            )
        job_id = m.group(1)

        res = cls(
            id=lease_id,
            name=name,
            host=host,
            job_id=job_id,
            submitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            persistent=persistent,
        )
        # Defer save() until allocation is confirmed. If the job times out
        # or fails, scancel and leave no orphan state file. (Leaking SLURM
        # jobs is what created a runaway 5-minute test cost on 2026-04-28.)
        try:
            res._wait_for_allocation(poll_interval=poll_interval, timeout=poll_timeout)
        except BaseException:
            # Best-effort cleanup of the SLURM job; raise the original error.
            try:
                subprocess.run(
                    [
                        "ssh",
                        host,
                        _wrap_in_login_shell(f"scancel {job_id}"),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except Exception:  # pragma: no cover — defensive
                pass
            raise
        res.save()
        return res

    def _wait_for_allocation(self, *, poll_interval: float, timeout: float) -> None:
        """Poll squeue until the job is RUNNING and a compute node is known."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state, node = self._squeue_state()
            if state == "RUNNING" and node:
                self.node = node
                self.save()
                return
            if state in ("FAILED", "CANCELLED", "COMPLETED", "TIMEOUT"):
                raise RuntimeError(
                    f"job {self.job_id} ended in state {state} before allocation"
                )
            time.sleep(poll_interval)
        raise TimeoutError(f"job {self.job_id} did not reach RUNNING within {timeout}s")

    def _squeue_state(self) -> tuple[str, str]:
        """Return (state, node) for the current job_id."""
        inner = f"squeue --jobs={self.job_id} --noheader --format='%T %N' 2>/dev/null"
        result = subprocess.run(
            ["ssh", self.host, _wrap_in_login_shell(inner)],
            capture_output=True,
            text=True,
        )
        line = (result.stdout or "").strip()
        if not line:
            return ("", "")
        parts = line.split(None, 1)
        state = parts[0]
        node = parts[1] if len(parts) > 1 else ""
        return state, node

    # ------------------------------------------------------------------
    # Exec / attach
    # ------------------------------------------------------------------

    def exec(
        self,
        cmd: str | list[str],
        *,
        capture: bool = True,
        check: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        """Run ``cmd`` inside the reservation via ``srun --jobid --overlap``.

        Each call pays one ssh handshake. SSH ControlMaster (out of scope
        for this module) amortizes that cost across many calls.
        """
        if isinstance(cmd, list):
            cmd_str = " ".join(_quote(c) for c in cmd)
        else:
            cmd_str = cmd
        inner = f"srun --jobid={self.job_id} --overlap bash -lc {_quote(cmd_str)}"
        return subprocess.run(
            ["ssh", self.host, _wrap_in_login_shell(inner)],
            capture_output=capture,
            text=True,
            check=check,
            timeout=timeout,
        )

    def attach(self, *, cmd: str = "bash", pty: bool = True) -> int:
        """Interactive attach via ``srun --jobid --pty``. Blocks until exit."""
        inner = (
            f"srun --jobid={self.job_id} "
            + ("--pty " if pty else "")
            + f"bash -lc {_quote(cmd)}"
        )
        ssh_args = ["ssh"]
        if pty:
            ssh_args.append("-t")
        ssh_args += [self.host, _wrap_in_login_shell(inner)]
        return subprocess.run(ssh_args).returncode

    # ------------------------------------------------------------------
    # Release
    # ------------------------------------------------------------------

    def release(self, *, missing_ok: bool = True) -> bool:
        """Cancel the SLURM job and remove the lease state file. Idempotent."""
        inner = f"scancel {self.job_id}"
        result = subprocess.run(
            ["ssh", self.host, _wrap_in_login_shell(inner)],
            capture_output=True,
            text=True,
        )
        ok = result.returncode == 0
        if not ok and not missing_ok:
            raise RuntimeError(f"scancel {self.job_id} failed: {result.stderr.strip()}")
        # Best-effort wait so a follow-up book() with the same name doesn't
        # race the still-RUNNING job. Capped at one short backoff.
        time.sleep(_DEFAULT_RELEASE_BACKOFF)
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass
        return ok

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"Reservation(id={self.id!r}, host={self.host!r}, "
            f"job_id={self.job_id!r}, node={self.node!r})"
        )
