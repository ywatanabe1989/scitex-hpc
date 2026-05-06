---
description: |
  [TOPIC] CLI Reference
  [DETAILS] scitex-hpc CLI subcommand catalog — `reservations` book/list/get/exec/refresh/attach/cancel; `mcp` start/doctor/list-tools/install; `skills` list/get/install; `install-shell-completion` / `print-shell-completion` for tab completion; introspection via `list-python-apis` and `mcp list-tools -v|-vv|-vvv`. Universal flags (-V/-h/--help-recursive/--json) and per-verb pass-throughs (--dry-run / -y / --gpus). Read this when wiring scitex-hpc into a workflow or scripting around the CLI.
tags: [scitex-hpc-cli-reference, scitex-hpc, scitex-package]
---

# scitex-hpc CLI Reference

Generated CLI surface — `scitex-hpc --help-recursive` is always authoritative;
this leaf is a curated overview.

## Top-level shape

```text
scitex-hpc [OPTIONS] COMMAND [ARGS]...
```

Universal options at every level (per `general/03_interface_02_cli/08`):

| Flag | Meaning |
|---|---|
| `-V`, `--version` | Print version and exit |
| `-h`, `--help` | Show help |
| `--help-recursive` | Recursive help dump (every subcommand) |
| `--json` | Emit structured JSON output (propagates to subcommands) |

## Reservations (the headline workflow)

Book a SLURM allocation once, `exec` many commands inside it. Every
reservation is a long-lived blocker job (`tail -f /dev/null` by default)
so the lease and queue position survive across operator workflows.

| Verb | Purpose |
|---|---|
| `reservations book NAME` | Submit a hold-job and (optionally) wait for RUNNING |
| `reservations list` | List active reservations (lease-file view) |
| `reservations get NAME` | One reservation as JSON |
| `reservations exec NAME 'CMD'` | Run `CMD` inside the allocation (returns stdout/stderr/exit) |
| `reservations refresh NAME` | Re-discover `job_id` via `squeue --name=...` (after walltime resubmit, or after a `book` poll-timeout) |
| `reservations attach NAME` | Open an interactive shell on the compute node |
| `reservations cancel NAME` | `scancel` + clear lease state |

### `book` flags

```text
NAME                          (positional) lease label
--host TEXT                   SSH host. Falls back to $SCITEX_HPC_HOST → ~/.scitex/hpc/config.yaml
--partition TEXT              SLURM partition
--cpus INTEGER                CPUs per task
--time TEXT                   Walltime (7-0, 1:00:00, …)
--mem TEXT                    Memory (e.g. 32G)
--nodelist NODE               Pin to a specific node
--account TEXT                SLURM account / project (e.g. punim2354)
--qos TEXT                    QOS tier (e.g. publiccpu)
--gpus SPEC                   GPU request, pass-through to --gpus=<SPEC>
                              Examples: 1, a100:2, h100:4
--persistent                  walltime auto-resubmit via SIGUSR1
--hold-body TEXT              Custom sbatch script body (default: tail -f /dev/null)
--tmux-server SOCKET          Bootstrap a long-lived tmux server as PID 1
--poll-timeout FLOAT          How long to poll for RUNNING (default 300s)
--poll-interval FLOAT         Poll cadence
--dry-run                     Print plan without sbatch'ing
-y, --yes                     Skip confirmation prompt
--json                        Emit JSON output
```

### Important behavior — `book` never auto-scancels

If `--poll-timeout` expires while the SLURM job is still PENDING, `book`
**saves the lease and returns** with `node = null`. The SLURM job stays
queued. Later, run `scitex-hpc reservations refresh NAME` to fill in
`node` once SLURM schedules it. Tear down only via `reservations
cancel`.

Rationale: reservations are intentionally idle blockers so an operator
can `exec` workloads on demand. An auto-scancel on poll timeout would
silently undo the operator's queue position on a busy partition — and
makes the CLI dangerous for agents that retry.

## Introspection

| Command | Purpose |
|---|---|
| `list-python-apis [-v\|-vv\|-vvv] [--json]` | Public Python API symbols |
| `mcp list-tools [-v\|-vv\|-vvv] [--json]` | MCP tools registered on this package |
| `mcp doctor` | Health-check the MCP server + fastmcp |

## MCP server commands (§3 four mandatory subcommands)

| Command | Purpose |
|---|---|
| `mcp start [--dry-run]` | Start the FastMCP server (stdio) |
| `mcp doctor` | Check fastmcp + server importability |
| `mcp list-tools [-v\|-vv\|-vvv] [--json]` | List registered tools |
| `mcp install [--json]` | Print install + claude-desktop config snippet |

## Skills group (`_skills/scitex-hpc/` access)

| Command | Purpose |
|---|---|
| `skills list [--json]` | List bundled skill files |
| `skills get NAME [--json]` | Print a skill's contents |
| `skills install [--claude-symlink] [--no-link] [--dry-run] [-y]` | Install to `~/.scitex/dev/skills/scitex-hpc/`; with `--claude-symlink`, also expose at `~/.claude/skills/scitex/` |

## Tab completion

Click-driven completion isn't auto-active. Run once and reload:

```bash
scitex-hpc install-shell-completion --shell bash   # → ~/.bashrc
source ~/.bashrc

# zsh / fish:
scitex-hpc install-shell-completion --shell zsh    # → ~/.zshrc
scitex-hpc install-shell-completion --shell fish   # → ~/.config/fish/completions/scitex-hpc.fish
```

`scitex-hpc print-shell-completion --shell SHELL` emits the script to
stdout if you'd rather wire it in by hand.

## See also

- `general/03_interface_02_cli/` — universal CLI conventions, banned leaves, audit rules.
- `[11_reservations-api.md](11_reservations-api.md)` — programmatic Python API for reservations.
- `[20_env-vars.md](20_env-vars.md)` — `SCITEX_HPC_*` environment overrides that match every flag above.
