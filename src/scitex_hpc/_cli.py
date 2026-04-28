"""scitex-hpc CLI — `scitex-hpc reservations <verb> ...`.

Phase 1 surface: book, list, get, exec, attach, release. The CLI is a
thin wrapper over :class:`scitex_hpc.Reservation`; everything it does is
also available as a Python API.

Future phases will add:
- ``reservations renew`` — extend a persistent lease (Phase 2)
- ``reservations migrate`` — adopt an existing SLURM job into a lease

CLI is implemented with argparse to avoid pulling in click as a runtime
dependency; sibling packages can use whichever they like.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__
from ._config import JobConfig
from ._reservation import Reservation


def _book(args: argparse.Namespace) -> int:
    cfg = JobConfig(
        project=args.name,
        host=args.host,
        partition=args.partition,
        cpus=args.cpus,
        time=args.time,
        mem=args.mem,
        job_name=args.name,
    )
    res = Reservation.book(
        cfg,
        persistent=args.persistent,
        hold_body=args.hold_body,
        tmux_server=args.tmux_server,
        poll_timeout=args.poll_timeout,
        poll_interval=args.poll_interval,
    )
    if args.json:
        print(json.dumps(_serialize(res), indent=2))
    else:
        print(f"booked: id={res.id} job={res.job_id} node={res.node}")
    return 0


def _list(args: argparse.Namespace) -> int:
    rows = Reservation.list()
    if args.json:
        print(json.dumps([_serialize(r) for r in rows], indent=2))
        return 0
    if not rows:
        print("(no reservations)")
        return 0
    fmt = "{:32}  {:10}  {:14}  {:30}"
    print(fmt.format("ID", "JOB", "PERSIST", "NODE"))
    for r in rows:
        print(
            fmt.format(r.id, r.job_id, "yes" if r.persistent else "no", r.node or "-")
        )
    return 0


def _get(args: argparse.Namespace) -> int:
    res = Reservation.get(args.name, host=args.host)
    if res is None:
        print(f"(no reservation named {args.name!r})", file=sys.stderr)
        return 2
    print(json.dumps(_serialize(res), indent=2))
    return 0


def _exec(args: argparse.Namespace) -> int:
    res = Reservation.require(args.name, host=args.host)
    out = res.exec(args.command)
    sys.stdout.write(out.stdout or "")
    sys.stderr.write(out.stderr or "")
    return out.returncode


def _attach(args: argparse.Namespace) -> int:
    res = Reservation.require(args.name, host=args.host)
    return res.attach(cmd=args.shell, pty=True)


def _refresh(args: argparse.Namespace) -> int:
    """Re-discover the current job_id via squeue --user --name=<friendly>.

    Useful after a walltime auto-resubmit (persistent=True): the SLURM
    job_id changes but the friendly name stays stable. The cached
    ``job_id`` in the lease file becomes stale until refreshed.
    """
    res = Reservation.require(args.name, host=args.host)
    res.refresh()
    if args.json:
        print(json.dumps(_serialize(res), indent=2))
    else:
        if res.job_id:
            print(f"refreshed: id={res.id} job={res.job_id} node={res.node}")
        else:
            print(
                f"refreshed: id={res.id} (no live job found via "
                f"squeue --name={res.name})",
                file=sys.stderr,
            )
            return 2
    return 0


def _release(args: argparse.Namespace) -> int:
    res = Reservation.get(args.name, host=args.host)
    if res is None:
        print(f"(no reservation named {args.name!r})", file=sys.stderr)
        return 0 if args.missing_ok else 2
    ok = res.release(missing_ok=True)
    print(f"released: {res.id} ({'ok' if ok else 'scancel-failed'})")
    return 0 if ok else 1


def _serialize(res: Reservation) -> dict:
    """Plain-dict view of a Reservation for JSON output."""
    return {
        "id": res.id,
        "name": res.name,
        "host": res.host,
        "job_id": res.job_id,
        "node": res.node,
        "submitted_at": res.submitted_at,
        "walltime_end": res.walltime_end,
        "persistent": res.persistent,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scitex-hpc",
        description="Generic SLURM dispatch + reservations.",
    )
    p.add_argument("--version", action="version", version=f"scitex-hpc {__version__}")
    sub = p.add_subparsers(dest="group", required=True)

    res = sub.add_parser("reservations", help="Persistent SLURM allocations")
    res_sub = res.add_subparsers(dest="verb", required=True)

    # book
    pb = res_sub.add_parser("book", help="Submit a hold-job and wait for allocation")
    pb.add_argument("name", help="Friendly lease name (e.g. dev-pool)")
    pb.add_argument("--host", required=True, help="SSH host (e.g. spartan)")
    pb.add_argument("--partition", default=None)
    pb.add_argument("--cpus", type=int, default=None)
    pb.add_argument("--time", default=None, help="walltime, e.g. 7-0 or 01:00:00")
    pb.add_argument("--mem", default=None)
    pb.add_argument(
        "--persistent", action="store_true", help="walltime auto-resubmit (Phase 2)"
    )
    pb.add_argument(
        "--hold-body",
        default=None,
        help="Custom sbatch script body (default: tail -f /dev/null)",
    )
    pb.add_argument(
        "--tmux-server",
        default=None,
        metavar="SOCKET",
        help=(
            "Bootstrap a long-lived tmux server with this socket name as "
            "the job's PID 1. Required for scitex-agent-container's "
            "slurm-tenant runtime (Phase 4). Example: --tmux-server sac"
        ),
    )
    pb.add_argument("--poll-timeout", type=float, default=300.0)
    pb.add_argument("--poll-interval", type=float, default=2.0)
    pb.add_argument("--json", action="store_true")
    pb.set_defaults(func=_book)

    # list
    pl = res_sub.add_parser("list", help="List active reservations")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=_list)

    # get
    pg = res_sub.add_parser("get", help="Show one reservation as JSON")
    pg.add_argument("name")
    pg.add_argument("--host", default=None)
    pg.set_defaults(func=_get)

    # exec
    pe = res_sub.add_parser("exec", help="Run a command inside the allocation")
    pe.add_argument("name")
    pe.add_argument("command")
    pe.add_argument("--host", default=None)
    pe.set_defaults(func=_exec)

    # refresh
    pr = res_sub.add_parser(
        "refresh",
        help=(
            "Re-discover the current job_id via squeue (after walltime auto-resubmit)"
        ),
    )
    pr.add_argument("name")
    pr.add_argument("--host", default=None)
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(func=_refresh)

    # attach
    pa = res_sub.add_parser(
        "attach", help="Open an interactive shell on the compute node"
    )
    pa.add_argument("name")
    pa.add_argument("--host", default=None)
    pa.add_argument("--shell", default="bash")
    pa.set_defaults(func=_attach)

    # release
    pr = res_sub.add_parser("release", help="scancel + clear lease state")
    pr.add_argument("name")
    pr.add_argument("--host", default=None)
    pr.add_argument(
        "--missing-ok",
        action="store_true",
        default=True,
        help="Exit 0 if the lease is already gone (default)",
    )
    pr.set_defaults(func=_release)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
