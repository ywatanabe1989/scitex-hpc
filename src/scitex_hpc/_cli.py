"""scitex-hpc CLI — `scitex-hpc reservations <verb> ...`.

Click-based CLI satisfying the SciTeX universal-flag contract:

* top-level: ``-V/--version``, ``-h/--help``, ``--help-recursive``, ``--json``
* ``mcp list-tools`` and ``list-python-apis`` introspection commands
* config-path fallback documented in root help

Subcommand surface:
    reservations book / list / get / exec / refresh / attach / cancel

``cancel`` is the canonical verb for tearing down a reservation
(``scancel`` + lease cleanup); the legacy ``release`` spelling is kept
as a hidden alias for one minor-version cycle.
"""

from __future__ import annotations

import json as _json
import sys
from typing import Sequence

import click

from . import __version__
from ._config import JobConfig
from ._reservation import Reservation

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


COMMAND_CATEGORIES = [
    ("Reservations", ["reservations"]),
    ("Introspection", ["list-python-apis", "mcp"]),
]


class CategorizedGroup(click.Group):
    """Click.Group that groups commands into named sections in --help."""

    def format_commands(self, ctx, formatter):
        commands = {}
        for name in self.list_commands(ctx):
            cmd = self.get_command(ctx, name)
            if cmd is not None and not cmd.hidden:
                commands[name] = cmd
        if not commands:
            return
        displayed = set()
        for category_name, category_commands in COMMAND_CATEGORIES:
            items = []
            for name in category_commands:
                if name in commands and name not in displayed:
                    cmd = commands[name]
                    items.append((name, cmd.get_short_help_str(limit=formatter.width)))
                    displayed.add(name)
            if items:
                with formatter.section(category_name):
                    formatter.write_dl(items)
        leftover = [
            (n, commands[n].get_short_help_str(limit=formatter.width))
            for n in sorted(commands.keys())
            if n not in displayed
        ]
        if leftover:
            with formatter.section("Other"):
                formatter.write_dl(leftover)


def _show_recursive_help(ctx: click.Context) -> None:
    click.echo(ctx.get_help())
    click.echo()
    group = ctx.command
    if isinstance(group, click.Group):
        for name in sorted(group.list_commands(ctx)):
            cmd = group.get_command(ctx, name)
            sub_ctx = click.Context(cmd, parent=ctx, info_name=name)
            click.echo("=" * 60)
            click.echo(f"Command: {name}")
            click.echo("=" * 60)
            click.echo(sub_ctx.get_help())
            click.echo()
            if isinstance(cmd, click.Group):
                for sub_name in sorted(cmd.list_commands(sub_ctx)):
                    sub_cmd = cmd.get_command(sub_ctx, sub_name)
                    sub_sub_ctx = click.Context(
                        sub_cmd, parent=sub_ctx, info_name=sub_name
                    )
                    click.echo("  " + "-" * 56)
                    click.echo(f"  Subcommand: {name} {sub_name}")
                    click.echo("  " + "-" * 56)
                    click.echo(sub_sub_ctx.get_help())
                    click.echo()


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


@click.group(
    cls=CategorizedGroup,
    invoke_without_command=True,
    context_settings=CONTEXT_SETTINGS,
)
@click.version_option(__version__, "-V", "--version", prog_name="scitex-hpc")
@click.help_option("-h", "--help")
@click.option(
    "--help-recursive",
    is_flag=True,
    help="Show help for all commands recursively.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit structured JSON output (propagates to subcommands that honour it).",
)
@click.pass_context
def cli(ctx: click.Context, help_recursive: bool, as_json: bool) -> None:
    """scitex-hpc - Generic SLURM dispatch + persistent reservations.

    \b
    Config is loaded with the SciTeX precedence chain:
      config.yaml -> $SCITEX_HPC_CONFIG -> ~/.scitex/hpc/config.yaml -> defaults
    """
    ctx.ensure_object(dict)
    ctx.obj["as_json"] = as_json
    if help_recursive:
        _show_recursive_help(ctx)
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---------------------------------------------------------------- reservations


@cli.group("reservations")
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
        "scheduling elsewhere. Useful when the operator needs to ssh "
        "into a known node (pam_slurm_adopt) or land on a machine "
        "with a specific hardware feature."
    ),
)
@click.option(
    "--account",
    default=None,
    help="SLURM account / project to bill (e.g. punim2354).",
)
@click.option(
    "--qos",
    default=None,
    help="SLURM QOS tier (e.g. publiccpu).",
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
        "runtime (Phase 4). Example: --tmux-server sac"
    ),
)
@click.option("--poll-timeout", type=float, default=300.0)
@click.option("--poll-interval", type=float, default=2.0)
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
    persistent,
    hold_body,
    tmux_server,
    poll_timeout,
    poll_interval,
    as_json,
):
    """Submit a hold-job and wait for SLURM allocation.

    \b
    Example:
      $ scitex-hpc reservations book dev-pool --host spartan --cpus 8 --mem 32G --time 7-0 --persistent
      $ scitex-hpc reservations book bm198-smoke --host spartan --nodelist spartan-bm198 --time 1:00:00 --account punim2354
    """
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
        job_name=name,
    )
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
    context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
)
@click.argument("name")
@click.argument("command")
@click.option("--host", default=None)
@click.pass_context
def exec_cmd(ctx, name, command, host):
    """Run a command inside the reservation's allocation.

    \b
    Example:
      $ scitex-hpc reservations exec dev-pool 'hostname'
      $ scitex-hpc reservations exec dev-pool 'python -m pytest'
    """
    res = Reservation.require(name, host=host)
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

    Useful for ``persistent=True`` leases: SLURM's ``job_id`` changes on
    auto-resubmit but the friendly name stays stable.

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
@click.pass_context
def cancel_cmd(ctx, name, host, missing_ok):
    """scancel + clear lease state for a reservation.

    \b
    Example:
      $ scitex-hpc reservations cancel dev-pool
      $ scitex-hpc reservations cancel dev-pool --no-missing-ok
    """
    _do_cancel(name, host, missing_ok, ctx)


@reservations.command("release", hidden=True)
@click.argument("name")
@click.option("--host", default=None)
@click.option("--missing-ok/--no-missing-ok", default=True)
@click.pass_context
def release_cmd(ctx, name, host, missing_ok):
    """(deprecated alias) Use ``reservations cancel`` instead."""
    _do_cancel(name, host, missing_ok, ctx)


# ---------------------------------------------------- introspection commands


@cli.command("list-python-apis")
@click.option("-v", "--verbose", count=True, help="Verbosity (-v, -vv).")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def list_python_apis(verbose, as_json):
    """List public Python API symbols of scitex_hpc.

    \b
    Example:
      $ scitex-hpc list-python-apis
      $ scitex-hpc list-python-apis --json
    """
    apis = [
        ("JobConfig", "Cluster-agnostic SLURM job configuration."),
        ("Reservation", "Persistent SLURM allocation handle."),
        ("srun", "Blocking interactive srun dispatch."),
        ("sbatch", "Async sbatch submission; returns job_id."),
        ("sync", "rsync local sources to the cluster."),
        ("poll_job", "Check sacct status for a job_id."),
        ("fetch_result", "scp the .out file of a finished sbatch job."),
    ]
    if as_json:
        click.echo(
            _json.dumps(
                {
                    "module": "scitex_hpc",
                    "apis": [{"name": n, "description": d} for n, d in apis],
                },
                indent=2,
            )
        )
        return
    click.echo("scitex_hpc Python API:")
    click.echo()
    for name, desc in apis:
        if verbose >= 1:
            click.echo(f"  {name:16s} {desc}")
        else:
            click.echo(f"  {name}")


@cli.group("mcp")
def mcp_group():
    """MCP (Model Context Protocol) server commands."""


@mcp_group.command("list-tools")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def mcp_list_tools(as_json):
    """List MCP tools exposed by scitex-hpc (none in current release).

    \b
    Example:
      $ scitex-hpc mcp list-tools
      $ scitex-hpc mcp list-tools --json
    """
    tools: list[tuple[str, str]] = []
    if as_json:
        click.echo(
            _json.dumps(
                {
                    "total": len(tools),
                    "tools": [{"name": n, "description": d} for n, d in tools],
                },
                indent=2,
            )
        )
        return
    if not tools:
        click.echo("(no MCP tools registered)")
        return
    for name, desc in tools:
        click.echo(f"  {name:20s} {desc}")


# ----------------------------------------------------- argv-style entry point


def main(argv: Sequence[str] | None = None) -> int:
    """Argv-style entry point.

    Kept for backward compatibility with ``main([...])`` tests and with the
    ``[project.scripts]`` installer hook. Delegates to the Click ``cli``
    group with ``standalone_mode=False`` so Click's ``SystemExit`` is
    surfaced as a return code instead of terminating the interpreter.
    """
    try:
        result = cli.main(
            args=list(argv) if argv is not None else None,
            prog_name="scitex-hpc",
            standalone_mode=False,
        )
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    except click.exceptions.Exit as e:
        return int(e.exit_code)
    except click.ClickException as e:
        e.show()
        return e.exit_code
    if isinstance(result, int):
        return result
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
