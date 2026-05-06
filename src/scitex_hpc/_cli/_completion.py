"""Shell-completion leaves: ``install-shell-completion`` + ``print-shell-completion``.

Inlined to avoid pulling ``scitex-dev`` into runtime deps.
"""

from __future__ import annotations

import os
import subprocess

import click

_SOURCE_MAP = {"bash": "bash_source", "zsh": "zsh_source", "fish": "fish_source"}


def _env_var(prog_name: str) -> str:
    return "_" + prog_name.upper().replace("-", "_") + "_COMPLETE"


def _rc_path(shell: str, prog_name: str) -> str:
    if shell == "fish":
        return os.path.expanduser(f"~/.config/fish/completions/{prog_name}.fish")
    return os.path.expanduser({"bash": "~/.bashrc", "zsh": "~/.zshrc"}[shell])


def _marker(prog_name: str) -> str:
    return f"# {prog_name} tab completion"


def _eval_line(shell: str, prog_name: str) -> str:
    return (
        f'eval "$({_env_var(prog_name)}={_SOURCE_MAP[shell]} {prog_name})"  '
        f"{_marker(prog_name)}"
    )


def _generate_script(shell: str, prog_name: str) -> str:
    env = os.environ.copy()
    env[_env_var(prog_name)] = _SOURCE_MAP[shell]
    result = subprocess.run([prog_name], capture_output=True, text=True, env=env)
    script = result.stdout.strip()
    if not script:
        raise click.ClickException(
            f"Failed to generate {shell} completion for {prog_name}."
        )
    return script


def attach_shell_completion(main_group, *, prog_name: str) -> None:
    """Register the canonical 4 shell-completion leaves on ``main_group``."""

    @main_group.command("print-shell-completion")
    @click.option(
        "--shell",
        type=click.Choice(["bash", "zsh", "fish"]),
        default="bash",
        help="Target shell. Default: bash.",
    )
    def print_shell_completion(shell):
        """Print the click-generated completion script to stdout.

        \b
        Example:
          $ scitex-hpc print-shell-completion --shell bash
          $ eval "$(scitex-hpc print-shell-completion --shell bash)"
        """
        click.echo(_generate_script(shell, prog_name))

    @main_group.command("install-shell-completion")
    @click.option(
        "--shell",
        type=click.Choice(["bash", "zsh", "fish"]),
        default="bash",
        help="Target shell. Default: bash.",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Print the eval line and target rc file without writing.",
    )
    @click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
    def install_shell_completion(shell, dry_run, yes):
        """Wire up ``<TAB>`` completion in the user's shell rc.

        \b
        Examples:
          scitex-hpc install-shell-completion              # → ~/.bashrc
          scitex-hpc install-shell-completion --shell zsh  # → ~/.zshrc
          scitex-hpc install-shell-completion --dry-run    # preview only

        \b
        Activate in the current shell after install:
          source ~/.bashrc
        """
        del yes
        rc_path = _rc_path(shell, prog_name)

        if shell == "fish":
            if dry_run:
                click.echo(f"Would write fish completion to {rc_path}")
                return
            os.makedirs(os.path.dirname(rc_path), exist_ok=True)
            with open(rc_path, "w") as f:
                f.write(_generate_script(shell, prog_name))
            click.echo(f"Tab completion installed at {rc_path}")
            click.echo(f"Run: source {rc_path}")
            return

        line = _eval_line(shell, prog_name)
        marker = _marker(prog_name)

        if dry_run:
            click.echo(f"Would append to {rc_path}:")
            click.echo(f"  {line}")
            return

        if os.path.isfile(rc_path):
            with open(rc_path) as f:
                if marker in f.read():
                    click.echo(f"Tab completion already installed in {rc_path}")
                    return
        with open(rc_path, "a") as f:
            f.write(f"\n{line}\n")
        click.echo(f"Tab completion installed in {rc_path}")
        click.echo(f"Run: source {rc_path}")

    @main_group.command(
        "install-tab-completion",
        hidden=True,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    )
    @click.pass_context
    def install_tab_completion_deprecated(ctx):
        """(deprecated) Renamed to ``install-shell-completion``."""
        click.echo(
            f"error: `{prog_name} install-tab-completion` was renamed to "
            f"`{prog_name} install-shell-completion`.",
            err=True,
        )
        ctx.exit(2)

    @main_group.command(
        "completion",
        hidden=True,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    )
    @click.pass_context
    def completion_deprecated(ctx):
        """(deprecated) Renamed to ``install-shell-completion``."""
        click.echo(
            f"error: `{prog_name} completion` was renamed to "
            f"`{prog_name} install-shell-completion`.",
            err=True,
        )
        ctx.exit(2)
