"""``list-python-apis`` introspection command (§1a)."""

from __future__ import annotations

import json as _json

import click


@click.command("list-python-apis")
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
