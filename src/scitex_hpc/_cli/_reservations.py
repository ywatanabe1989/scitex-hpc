"""``scitex-hpc reservations`` group — book / list / get / exec / refresh / attach / cancel.

``cancel`` is the canonical verb for tearing down a reservation
(``scancel`` + lease cleanup); the legacy ``release`` spelling is kept
as a hidden alias for one minor-version cycle.
"""

from __future__ import annotations

import json as _json
import sys

import click

from .._config import JobConfig
from .._reservation import Reservation


def _serialize(res: Reservation) -> dict:
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


@click.group("reservations")
def reservations() -> None:
    """Persistent SLURM allocations (book once, exec many)."""


@reservations.command("book")
@click.argument("name")
@click.option(
    "--host",
    default=None,
    help=(
        "SSH host to submit sbatch from (e.g. spartan). Optional — "
        "falls back to $SCITEX_HPC_HOST or "
        "~/.scitex/hpc/config.yaml's `host:` if unset, so an operator "
        "with a single cluster doesn't need to pass this every time."
    ),
)
@click.option("--partition", default=None)
@click.option("--cpus", type=int, default=None)
@click.option("--time", "time_", default=None, help="walltime, e.g. 7-0 or 01:00:00")
@click.option("--mem", default=None)
@click.option(
    "--nodelist",
    default=None,
    metavar="NODE",
    help=(
        "Pin the allocation to a specific node (e.g. spartan-bm198). "
        "SLURM will wait for the named node to free up rather than "
        "scheduling elsewhere."
    ),
)
@click.option(
    "--account",
    default=None,
    help="SLURM account / project to bill (e.g. punim2354).",
)
@click.option("--qos", default=None, help="SLURM QOS tier (e.g. publiccpu).")
@click.option(
    "--gpus",
    default=None,
    metavar="SPEC",
    help=(
        "Request GPUs. Pass-through to SLURM's --gpus=<SPEC>. "
        "Examples: '1' (any 1 GPU), 'a100:2' (two A100s), 'h100:4'. "
        "Omit for CPU-only allocations."
    ),
)
@click.option(
    "--persistent",
    is_flag=True,
    help="walltime auto-resubmit via SIGUSR1 (Phase 2).",
)
@click.option(
    "--hold-body",
    default=None,
    help="Custom sbatch script body (default: tail -f /dev/null).",
)
@click.option(
    "--tmux-server",
    default=None,
    metavar="SOCKET",
    help=(
        "Bootstrap a long-lived tmux server with this socket name as the "
        "job's PID 1. Required for scitex-agent-container's slurm-tenant "
        "runtime. Example: --tmux-server sac"
    ),
)
@click.option("--poll-timeout", type=float, default=300.0)
@click.option("--poll-interval", type=float, default=2.0)
@click.option("--dry-run", is_flag=True, help="Print plan without sbatch'ing.")
@click.option("-y", "--yes", "yes", is_flag=True, help="Skip confirmation prompt.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def book_cmd(
    name,
    host,
    partition,
    cpus,
    time_,
    mem,
    nodelist,
    account,
    qos,
    gpus,
    persistent,
    hold_body,
    tmux_server,
    poll_timeout,
    poll_interval,
    dry_run,
    yes,
    as_json,
):
    """Submit a hold-job and wait for SLURM allocation.

    \b
    Naming convention (use this for NAME — encodes the shape so any
    operator can read it without re-discovering the allocation):
      CPU-only : <cluster>-cpu-<cores>-cores-<ram_gb>-ram
                  e.g. spartan-cpu-64-cores-256-ram
      GPU      : <cluster>-gpu-<cores>-cores-<ram_gb>-ram-<vram_gb>-vram-<gputype>
                  e.g. spartan-gpu-16-cores-128-ram-80-vram-h100
                       spartan-gpu-8-cores-128-ram-80-vram-a100
    Suffix the GPU type at the end so similar-shape allocations on
    different GPU classes don't collide (always include it for GPU
    reservations). For multiple GPUs, use `-NxTYPE` (e.g.
    `...-80-vram-4xh100`). Always embed the cluster prefix so multi-
    cluster operators don't collide on the same lease id.

    Verified-working examples on Spartan (account=punim2354):

    \b
      # CPU — cascade partition (64 cores / 256 GB / 7 days)
      $ scitex-hpc reservations book spartan-cpu-64-cores-256-ram \\
          --partition cascade \\
          --cpus 64 --mem 256G --time 7-0 \\
          --account punim2354 --qos publiccpu --persistent

    \b
      # CPU — sapphire partition (64 cores / 128 GB / 7 days)
      $ scitex-hpc reservations book spartan-cpu-64-cores-128-ram \\
          --partition sapphire \\
          --cpus 64 --mem 128G --time 7-0 \\
          --account punim2354 --qos publiccpu --persistent

    \b
      # GPU — 1× H100 (16 cores / 128 GB / 7 days)
      $ scitex-hpc reservations book spartan-gpu-16-cores-128-ram-80-vram-h100 \\
          --partition gpu-h100 \\
          --cpus 16 --mem 128G --gpus H100:1 --time 7-0 \\
          --account punim2354 --qos feit --persistent

    \b
      # GPU — 1× A100 (8 cores / 128 GB / 7 days)
      $ scitex-hpc reservations book spartan-gpu-8-cores-128-ram-80-vram-a100 \\
          --partition gpu-a100 \\
          --cpus 8 --mem 128G --gpus A100:1 --time 7-0 \\
          --account punim2354 --qos publicgpu --persistent

    \b
    Spartan gotchas (learned the hard way):
      * --gpus value is case-sensitive on Spartan: use UPPERCASE
        `H100:1`, `A100:1` — lowercase fails with
        "Requested node configuration is not available".
      * Per-GPU caps on shared nodes:
          - gpu-a100 nodes: 32 cores / 512 GB / 4 A100  → max 8 cores
            and 128 GB per A100.
          - gpu-h100 nodes: 64 cores / 1 TB  / 4 H100   → max 16 cores
            and ~256 GB per H100.
        Asking above these caps for a 1-GPU job is rejected.
      * QOS by partition (account=punim2354):
          - cascade / sapphire (CPU)  → --qos publiccpu
          - gpu-a100                  → --qos publicgpu
          - gpu-h100                  → --qos feit
    """
    del yes
    cfg = JobConfig(
        project=name,
        host=host,
        partition=partition,
        cpus=cpus,
        time=time_,
        mem=mem,
        nodelist=nodelist,
        account=account,
        qos=qos,
        gpus=gpus,
        job_name=name,
    )
    if dry_run:
        plan = {
            "would_book": name,
            "config": {
                "host": cfg.host,
                "partition": cfg.partition,
                "cpus": cfg.cpus,
                "time": cfg.time,
                "mem": cfg.mem,
                "nodelist": cfg.nodelist,
                "account": cfg.account,
                "qos": cfg.qos,
                "gpus": cfg.gpus,
            },
            "persistent": persistent,
            "tmux_server": tmux_server,
        }
        click.echo(_json.dumps(plan, indent=2))
        return
    res = Reservation.book(
        cfg,
        persistent=persistent,
        hold_body=hold_body,
        tmux_server=tmux_server,
        poll_timeout=poll_timeout,
        poll_interval=poll_interval,
    )
    if as_json:
        click.echo(_json.dumps(_serialize(res), indent=2))
    else:
        click.echo(f"booked: id={res.id} job={res.job_id} node={res.node}")


@reservations.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def list_cmd(as_json):
    """List active reservations.

    \b
    Example:
      $ scitex-hpc reservations list
      $ scitex-hpc reservations list --json
    """
    rows = Reservation.list()
    if as_json:
        click.echo(_json.dumps([_serialize(r) for r in rows], indent=2))
        return
    if not rows:
        click.echo("(no reservations)")
        return
    fmt = "{:32}  {:10}  {:14}  {:30}"
    click.echo(fmt.format("ID", "JOB", "PERSIST", "NODE"))
    for r in rows:
        click.echo(
            fmt.format(r.id, r.job_id, "yes" if r.persistent else "no", r.node or "-")
        )


@reservations.command("get")
@click.argument("name")
@click.option("--host", default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
@click.pass_context
def get_cmd(ctx, name, host, as_json):
    """Show one reservation as JSON.

    \b
    Example:
      $ scitex-hpc reservations get dev-pool
      $ scitex-hpc reservations get dev-pool --json
    """
    res = Reservation.get(name, host=host)
    if res is None:
        click.echo(f"(no reservation named {name!r})", err=True)
        ctx.exit(2)
    click.echo(_json.dumps(_serialize(res), indent=2))


@reservations.command(
    "exec",
    context_settings={
        "ignore_unknown_options": True,
        "allow_interspersed_args": False,
    },
)
@click.argument("name")
@click.argument("command")
@click.option("--host", default=None)
@click.option("--dry-run", is_flag=True, help="Print plan without ssh-execing.")
@click.option("-y", "--yes", "yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def exec_cmd(ctx, name, command, host, dry_run, yes):
    """Run a command inside the reservation's allocation.

    \b
    Example:
      $ scitex-hpc reservations exec dev-pool 'hostname'
      $ scitex-hpc reservations exec dev-pool 'python -m pytest'
    """
    del yes
    res = Reservation.require(name, host=host)
    if dry_run:
        click.echo(f"DRY RUN — would exec on {res.id}: {command}")
        return
    out = res.exec(command)
    sys.stdout.write(out.stdout or "")
    sys.stderr.write(out.stderr or "")
    ctx.exit(out.returncode)


@reservations.command("refresh")
@click.argument("name")
@click.option("--host", default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
@click.pass_context
def refresh_cmd(ctx, name, host, as_json):
    """Re-discover the current job_id via squeue (after walltime auto-resubmit).

    \b
    Example:
      $ scitex-hpc reservations refresh dev-pool
      $ scitex-hpc reservations refresh dev-pool --json
    """
    res = Reservation.require(name, host=host)
    res.refresh()
    if as_json:
        click.echo(_json.dumps(_serialize(res), indent=2))
        return
    if res.job_id:
        click.echo(f"refreshed: id={res.id} job={res.job_id} node={res.node}")
    else:
        click.echo(
            f"refreshed: id={res.id} (no live job found via squeue --name={res.name})",
            err=True,
        )
        ctx.exit(2)


@reservations.command("attach")
@click.argument("name")
@click.option("--host", default=None)
@click.option("--shell", default="bash")
@click.pass_context
def attach_cmd(ctx, name, host, shell):
    """Open an interactive shell on the reservation's compute node.

    \b
    Example:
      $ scitex-hpc reservations attach dev-pool
      $ scitex-hpc reservations attach dev-pool --shell zsh
    """
    res = Reservation.require(name, host=host)
    rc = res.attach(cmd=shell, pty=True)
    ctx.exit(rc)


def _do_cancel(name, host, missing_ok, ctx):
    res = Reservation.get(name, host=host)
    if res is None:
        click.echo(f"(no reservation named {name!r})", err=True)
        ctx.exit(0 if missing_ok else 2)
    ok = res.release(missing_ok=True)
    click.echo(f"released: {res.id} ({'ok' if ok else 'scancel-failed'})")
    ctx.exit(0 if ok else 1)


@reservations.command("cancel")
@click.argument("name")
@click.option("--host", default=None)
@click.option(
    "--missing-ok/--no-missing-ok",
    default=True,
    help="Exit 0 if the lease is already gone (default).",
)
@click.option("--dry-run", is_flag=True, help="Print plan without scancel'ing.")
@click.option("-y", "--yes", "yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def cancel_cmd(ctx, name, host, missing_ok, dry_run, yes):
    """scancel + clear lease state for a reservation.

    \b
    Example:
      $ scitex-hpc reservations cancel dev-pool
      $ scitex-hpc reservations cancel dev-pool --no-missing-ok
    """
    del yes
    if dry_run:
        click.echo(f"DRY RUN — would cancel reservation {name!r}")
        return
    _do_cancel(name, host, missing_ok, ctx)


@reservations.command("release", hidden=True)
@click.argument("name")
@click.option("--host", default=None)
@click.option("--missing-ok/--no-missing-ok", default=True)
@click.pass_context
def release_cmd(ctx, name, host, missing_ok):
    """(deprecated alias) Use ``reservations cancel`` instead."""
    _do_cancel(name, host, missing_ok, ctx)
