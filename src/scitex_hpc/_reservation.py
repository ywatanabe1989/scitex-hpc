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

from scitex_config._ecosystem import local_state
from scitex_ssh import exec_remote as _default_exec_remote

from ._config import JobConfig
from ._dispatch import _quote, _wrap_in_login_shell


# Test-injection seams. Tests pass real fake callables via the
# ``runner=`` / ``attach_runner=`` / ``sleep=`` / ``monotonic=`` kwargs
# on ``Reservation.book`` / ``from_jobid`` (which store them on the
# returned instance) and on the per-method overrides for ``exec``,
# ``release``, ``refresh``, ``attach``. Production paths use the
# defaults below. No monkeypatching, no module-level swaps.
def _default_runner(host, command, *, check=False, timeout=None):
    """Default ssh runner — calls real ``scitex_ssh.exec_remote``."""
    return _default_exec_remote(host, command, check=check, timeout=timeout)


def _default_attach_runner(args):
    """Default local subprocess runner for ``attach()`` (interactive ssh)."""
    return subprocess.run(args)


def _default_sleep(seconds):
    time.sleep(seconds)


def _default_monotonic():
    return time.monotonic()


def _override_defaults(
    *,
    runner=None,
    attach_runner=None,
    sleep=None,
    monotonic=None,
):
    """Yield-based context manager that swaps the module-level
    ``_default_*`` collaborators for the duration of a ``with`` block.

    This is the seam the CLI tests use to inject fakes: the CLI path
    calls ``Reservation.book(...)``/``require(...)`` without runner
    kwargs, so the new Reservation instances pick up whatever
    ``_default_runner`` resolves to at construction time.

    Production code never calls this. Tests use it via:

        with _override_defaults(runner=fake): main([...])

    No mocks, no ``monkeypatch`` — real module-attribute mutation with
    guaranteed restoration on exit.
    """
    import contextlib

    @contextlib.contextmanager
    def _cm():
        g = globals()
        prior = {
            "_default_runner": g["_default_runner"],
            "_default_attach_runner": g["_default_attach_runner"],
            "_default_sleep": g["_default_sleep"],
            "_default_monotonic": g["_default_monotonic"],
        }
        if runner is not None:
            g["_default_runner"] = runner
        if attach_runner is not None:
            g["_default_attach_runner"] = attach_runner
        if sleep is not None:
            g["_default_sleep"] = sleep
        if monotonic is not None:
            g["_default_monotonic"] = monotonic
        try:
            yield
        finally:
            for k, v in prior.items():
                g[k] = v

    return _cm()


# Hold body: background a long-sleep and `wait` on it. The `wait` builtin
# is interruptible by trapped signals, so SIGUSR1 reaches the trap as
# soon as SLURM sends it; the previous `tail -f /dev/null` foreground
# pattern blocked bash in a system `wait()` that ignored SIGUSR1 until
# `tail` itself exited (which never happened before SLURM hard-killed
# the job, breaking the auto-resubmit chain).
_DEFAULT_HOLD_BODY = "sleep infinity &\nwait $!"
_LEASE_DIR_ENV = "SCITEX_HPC_LEASE_DIR"
_DEFAULT_POLL_INTERVAL = 2.0
_DEFAULT_POLL_TIMEOUT = 300.0
_DEFAULT_RELEASE_BACKOFF = 0.5

# Walltime auto-resubmit (Phase 2). The trap fires N seconds before
# walltime via ``#SBATCH --signal=B:USR1@<N>`` and resubmits the script
# in place via ``sbatch "$0"``. SLURM's documented signaling mechanism —
# not a custom daemon, so it's compatible with the 2026-04-26 IT Security
# ruling on Spartan.
_RESUBMIT_LEAD_SECONDS = 3600  # request signal 1h before walltime
_RESUBMIT_SIGNAL = f"B:USR1@{_RESUBMIT_LEAD_SECONDS}"
_USR1_TRAP_FUNC = "_scitex_hpc_walltime_resubmit"


def _lease_dir() -> Path:
    """Return ``~/.scitex/hpc/leases/`` (override via ``SCITEX_HPC_LEASE_DIR``)."""
    override = os.environ.get(_LEASE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return local_state.runtime_path("hpc", "leases")


def _make_lease_id(host: str, name: str) -> str:
    """Lease id is ``<host>-<name>``. Names must be filesystem-safe."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", name)
    return f"{host}-{safe}"


# SLURM job-state vocabulary. Used as a filter to ignore login-shell
# banner lines (DISPLAY:, XAUTHORITY:, etc.) that prefix the real squeue
# output on chatty hosts like Spartan.
_SLURM_STATES: frozenset[str] = frozenset(
    {
        "RUNNING",
        "PENDING",
        "COMPLETING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "TIMEOUT",
        "OUT_OF_MEMORY",
        "BOOT_FAIL",
        "DEADLINE",
        "NODE_FAIL",
        "PREEMPTED",
        "REVOKED",
        "SUSPENDED",
        "STOPPED",
        "CONFIGURING",
        "RESIZING",
        "REQUEUED",
    }
)


def _parse_squeue_state_node(stdout: str) -> tuple[str, str]:
    """Parse ``squeue --noheader --format='%T %N'`` output.

    Robust against login-shell banner noise: scans for the first line
    whose first whitespace-delimited token is a known SLURM state, and
    returns ``(state, node)``. Returns ``("", "")`` if no such line.
    """
    for line in stdout.splitlines():
        parts = line.strip().split(None, 1)
        if not parts or parts[0] not in _SLURM_STATES:
            continue
        state = parts[0]
        node = parts[1] if len(parts) > 1 else ""
        return state, node
    return ("", "")


def _tmux_server_bootstrap(socket: str) -> str:
    """Generate a hold-body fragment that starts a long-lived tmux server.

    The fragment must be prepended to whatever hold body keeps the job alive
    (typically ``tail -f /dev/null``). Example assembled body::

        tmux -L sac new-session -d -s _root 'sleep infinity'
        tail -f /dev/null

    Why this is necessary for multi-tenant agents (Phase 4 of #1):

    SLURM kills *all processes in a step's cgroup* when the step ends.
    A tmux daemon spawned by ``srun --jobid --overlap`` therefore dies
    immediately — verified live on spartan-bm021 2026-04-28.

    But a tmux server started by the **sbatch script itself** (the job's
    main process) lives in the job's cgroup, not a transient step's
    cgroup. It survives as long as the script's tail/sleep keeps the job
    alive. Tenants can then ``tmux -L <socket> new-session -t _root ...``
    against the same socket and their sessions survive too.

    The socket name is per-job, so multiple reservations on the same
    compute node can coexist with different sockets.
    """
    safe_socket = re.sub(r"[^A-Za-z0-9._-]", "-", socket)
    return (
        f"# scitex-hpc tmux server bootstrap (Phase 4 multi-tenant support)\n"
        f"tmux -L {safe_socket} new-session -d -s _root "
        f"'sleep infinity' 2>/dev/null || true\n"
    )


def _wrap_with_resubmit_trap(hold_body: str) -> str:
    """Wrap a sbatch script body with the SIGUSR1 walltime auto-resubmit trap.

    The trap calls ``sbatch "$0"`` to resubmit the script in place.
    SLURM signals USR1 at ``--signal=B:USR1@<N>`` so the trap fires N
    seconds before walltime expires; the new job lands in the queue
    while the old one is still running, so the allocation is effectively
    permanent (modulo cluster-side intervention).

    The trap function is named uniquely to avoid colliding with anything
    the user's hold body might define.
    """
    trap = (
        f"{_USR1_TRAP_FUNC}() {{\n"
        f'  echo "[scitex-hpc] walltime approaching; resubmitting via sbatch $0" >&2\n'
        f'  sbatch "$0"\n'
        f"}}\n"
        f"trap {_USR1_TRAP_FUNC} USR1\n"
    )
    return f"{trap}{hold_body}"


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

    def __post_init__(self) -> None:
        # Injection seams — non-dataclass attrs (not serialised by asdict).
        # Tests inject real fake callables; production uses the module
        # defaults bound to the real subprocess / ssh primitives.
        self._runner = _default_runner
        self._attach_runner = _default_attach_runner
        self._sleep = _default_sleep
        self._monotonic = _default_monotonic

    def with_collaborators(
        self,
        *,
        runner=None,
        attach_runner=None,
        sleep=None,
        monotonic=None,
    ) -> "Reservation":
        """Bind test-supplied collaborators to this instance.

        Returns ``self`` so callers can chain. Production code does not
        call this — defaults are bound in ``__post_init__``.
        """
        if runner is not None:
            self._runner = runner
        if attach_runner is not None:
            self._attach_runner = attach_runner
        if sleep is not None:
            self._sleep = sleep
        if monotonic is not None:
            self._monotonic = monotonic
        return self

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
    # Adoption (Phase 3 enabler)
    # ------------------------------------------------------------------

    @classmethod
    def from_jobid(
        cls,
        *,
        host: str,
        job_id: str,
        name: str,
        persistent: bool = False,
        save: bool = True,
        refresh_node: bool = True,
        runner=None,
    ) -> "Reservation":
        """Adopt an *already-submitted* SLURM job into a Reservation.

        Use case: consumers (e.g. scitex-agent-container's SlurmRuntime)
        that build their own sbatch scripts with custom hardeners can
        still surface as Reservations after submission. They run their
        own ``sbatch …`` to get the job_id, then call ``from_jobid(...)``
        to write the lease file and gain the Reservation API surface
        (exec, attach, refresh, release, list).

        Refuses to overwrite an existing lease (use ``release()`` first
        if you want to re-adopt a different job under the same name).

        If ``refresh_node=True`` (default), polls squeue once to populate
        the ``node`` field. Pass False to skip the network round-trip
        when the caller already knows the node.
        """
        if not host:
            raise ValueError("from_jobid requires non-empty host")
        if not str(job_id).strip():
            raise ValueError("from_jobid requires non-empty job_id")
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("from_jobid requires non-empty name")
        lease_id = _make_lease_id(host, clean_name)

        existing = cls.get(lease_id)
        if existing is not None:
            raise FileExistsError(
                f"reservation {lease_id} already exists; release it first"
            )

        res = cls(
            id=lease_id,
            name=clean_name,
            host=host,
            job_id=str(job_id),
            submitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            persistent=persistent,
        )
        if runner is not None:
            res.with_collaborators(runner=runner)
        if refresh_node:
            try:
                _, node = res._squeue_state()
                res.node = node
            except Exception:  # pragma: no cover — defensive
                pass
        if save:
            res.save()
        return res

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
        tmux_server: str | None = None,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
        runner=None,
        attach_runner=None,
        sleep=None,
        monotonic=None,
    ) -> "Reservation":
        """Submit a hold-job, wait for SLURM to allocate a node, return Reservation.

        ``hold_body`` is the body of the sbatch script. Defaults to
        ``tail -f /dev/null`` so the allocation persists. Pass a custom body
        when a consumer (e.g. scitex-agent-container) needs to set up tmux,
        traps, etc. on the compute node before the hold.

        ``persistent=True`` enables walltime auto-resubmit: the sbatch
        wrapper installs a SIGUSR1 trap that calls ``sbatch "$0"`` shortly
        before walltime expires (via ``#SBATCH --signal=B:USR1@3600``).
        The lease keeps its friendly name across resubmits; the SLURM
        ``job_id`` changes, so use ``Reservation.refresh()`` to update
        the cached job_id before ``exec()`` / ``attach()`` calls that
        cross a resubmit boundary.

        ``tmux_server`` (Phase 4): if set to a socket name (e.g. ``"sac"``),
        the sbatch script bootstraps a tmux server via
        ``tmux -L <name> new-session -d -s _root 'sleep infinity'`` BEFORE
        the hold body runs. This makes the tmux server a child of the
        sbatch script (the job's main process), so it lives in the job's
        cgroup — surviving past any ``srun --jobid --overlap`` step that
        would otherwise terminate it. Tenants then attach via
        ``tmux -L <name> ...`` and their sessions outlive their setup
        commands. The socket name is stored in the Reservation as
        ``extras["tmux_server"]`` so consumers can rediscover it.
        """
        runner = runner or _default_runner
        sleep_fn = sleep or _default_sleep
        monotonic_fn = monotonic or _default_monotonic
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
        if tmux_server:
            body = _tmux_server_bootstrap(tmux_server) + body
        if persistent:
            body = _wrap_with_resubmit_trap(body)
        sbatch_args = [*config.slurm_args(), *config.extra_sbatch_args]
        if persistent:
            # SLURM sends SIGUSR1 to the batch script N seconds before walltime;
            # the trap wired into ``body`` resubmits ``$0``.
            sbatch_args.append(f"--signal={_RESUBMIT_SIGNAL}")
        script_body = (
            "#!/bin/bash\n#SBATCH " + "\n#SBATCH ".join(sbatch_args) + f"\n{body}\n"
        )
        if persistent:
            # Materialise the script to a durable per-lease path so the
            # SIGUSR1 trap's `sbatch "$0"` resubmits a real file instead
            # of a transient process-substitution FD. Without this, the
            # auto-resubmit chain dies silently the first time it fires
            # because `$0` is `/proc/self/fd/<N>` whose pipe is already
            # closed.
            script_dir = "~/.scitex/hpc/scripts"
            script_path = f"{script_dir}/{lease_id}.sh"
            inner = (
                f"mkdir -p {script_dir} && "
                f"cat > {script_path} <<'_SCITEX_HPC_EOF_'\n"
                f"{script_body}"
                f"_SCITEX_HPC_EOF_\n"
                f"chmod +x {script_path} && "
                f"sbatch {script_path}"
            )
        else:
            # One-shot path keeps the original process-substitution shape
            # because `$0` won't be reused (no resubmit trap).
            inner = f"sbatch <(printf %s {_quote(script_body)})"
        result = runner(host, _wrap_in_login_shell(inner))
        if result.returncode != 0:
            raise RuntimeError(f"sbatch failed: {result.stderr.strip()}")
        m = re.search(r"Submitted batch job (\d+)", result.stdout)
        if not m:
            raise RuntimeError(
                f"could not parse jobid from sbatch output: {result.stdout!r}"
            )
        job_id = m.group(1)

        extras: dict[str, Any] = {}
        if tmux_server:
            extras["tmux_server"] = tmux_server
        res = cls(
            id=lease_id,
            name=name,
            host=host,
            job_id=job_id,
            submitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            persistent=persistent,
            extras=extras,
        )
        res.with_collaborators(
            runner=runner,
            attach_runner=attach_runner,
            sleep=sleep_fn,
            monotonic=monotonic_fn,
        )
        # Save the lease immediately so the SLURM job is recoverable even
        # if poll-for-allocation times out. Reservations are intentionally
        # long-lived blockers (tail -f /dev/null) — auto-scancelling on
        # poll timeout defeats the entire pattern. Run `reservations
        # refresh <name>` later to fill in the node once SLURM schedules it.
        res.save()
        try:
            res._wait_for_allocation(poll_interval=poll_interval, timeout=poll_timeout)
        except TimeoutError:
            # Job is still queued; lease persists with empty `node`. User
            # can `reservations refresh` once SLURM schedules it. Never
            # scancel here.
            pass
        return res

    def _wait_for_allocation(self, *, poll_interval: float, timeout: float) -> None:
        """Poll squeue until the job is RUNNING and a compute node is known."""
        deadline = self._monotonic() + timeout
        while self._monotonic() < deadline:
            state, node = self._squeue_state()
            if state == "RUNNING" and node:
                self.node = node
                self.save()
                return
            if state in ("FAILED", "CANCELLED", "COMPLETED", "TIMEOUT"):
                raise RuntimeError(
                    f"job {self.job_id} ended in state {state} before allocation"
                )
            self._sleep(poll_interval)
        raise TimeoutError(f"job {self.job_id} did not reach RUNNING within {timeout}s")

    def _squeue_state(self) -> tuple[str, str]:
        """Return (state, node) for the current job_id.

        Robust against chatty ``.bashrc`` banners: many HPC login shells
        emit lines like ``DISPLAY: 1.2.3.4:0`` before user commands run.
        We scan stdout line-by-line and pick the line whose first token
        looks like a SLURM state. (Discovered live on Spartan 2026-04-28
        when book() polled forever because parts[0] resolved to
        ``"XAUTHORITY:"`` instead of ``"RUNNING"``.)
        """
        inner = f"squeue --jobs={self.job_id} --noheader --format='%T %N' 2>/dev/null"
        result = self._runner(self.host, _wrap_in_login_shell(inner))
        return _parse_squeue_state_node(result.stdout or "")

    # ------------------------------------------------------------------
    # Refresh (Phase 2: walltime auto-resubmit)
    # ------------------------------------------------------------------

    def refresh(self, *, save: bool = True) -> "Reservation":
        """Re-discover the current ``job_id`` and ``node`` via squeue.

        Persistent reservations resubmit themselves shortly before walltime
        via the SIGUSR1 trap, so the SLURM ``job_id`` changes periodically.
        Friendly name (and lease id) stays stable. ``refresh()`` queries
        ``squeue --user=$USER --name=<friendly-name>`` and updates the
        cached job_id / node in place.

        Returns ``self`` for chaining; if ``save=True`` (default) the new
        state is persisted to the lease file.

        Behavior in edge cases:
        - No matching job in queue → fields cleared (job_id="", node="")
        - Multiple matching jobs (resubmit overlap window) → picks the
          newest jobid, since the older one is about to exit
        """
        inner = (
            f"squeue --user=$USER --name={_quote(self.name)} "
            "--noheader --format='%i %T %N' 2>/dev/null"
        )
        result = self._runner(self.host, _wrap_in_login_shell(inner))
        rows: list[tuple[int, str, str]] = []
        for line in (result.stdout or "").splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 2 or not parts[0].isdigit():
                continue
            jobid_int = int(parts[0])
            state = parts[1]
            node = parts[2] if len(parts) > 2 else ""
            rows.append((jobid_int, state, node))
        if not rows:
            self.job_id = ""
            self.node = ""
        else:
            # Newest jobid wins — the resubmit-overlap window has the
            # outgoing (older) job and the incoming (newer) one; we want
            # the one that will keep running.
            rows.sort(key=lambda t: t[0], reverse=True)
            best = rows[0]
            self.job_id = str(best[0])
            self.node = best[2]
        if save and self.job_id:
            self.save()
        return self

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
    ):
        """Run ``cmd`` inside the reservation via ``srun --jobid --overlap``.

        Each call pays one ssh handshake. SSH ControlMaster (out of scope
        for this module) amortizes that cost across many calls.

        Returns an object with ``returncode``, ``stdout``, ``stderr`` (a
        ``scitex_ssh.SSHResult``). The ``capture`` argument is accepted for
        backward compatibility but ignored — output is always captured.
        """
        del capture  # always captured by scitex_ssh.exec_remote
        if isinstance(cmd, list):
            cmd_str = " ".join(_quote(c) for c in cmd)
        else:
            cmd_str = cmd
        inner = f"srun --jobid={self.job_id} --overlap bash -lc {_quote(cmd_str)}"
        return self._runner(
            self.host,
            _wrap_in_login_shell(inner),
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
        return self._attach_runner(ssh_args).returncode

    # ------------------------------------------------------------------
    # Release
    # ------------------------------------------------------------------

    def release(self, *, missing_ok: bool = True) -> bool:
        """Cancel the SLURM job and remove the lease state file. Idempotent."""
        inner = f"scancel {self.job_id}"
        result = self._runner(self.host, _wrap_in_login_shell(inner))
        ok = result.returncode == 0
        if not ok and not missing_ok:
            raise RuntimeError(f"scancel {self.job_id} failed: {result.stderr.strip()}")
        # Best-effort wait so a follow-up book() with the same name doesn't
        # race the still-RUNNING job. Capped at one short backoff.
        self._sleep(_DEFAULT_RELEASE_BACKOFF)
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
