# scitex-hpc — examples

Minimal end-to-end recipes that exercise the public surface.

| File | What it shows |
|---|---|
| [`01_book_persistent_reservation.py`](01_book_persistent_reservation.py) | Programmatic `Reservation.book(...)` — the Python equivalent of `scitex-hpc reservations book ... --persistent`. |
| [`00_run_all.sh`](00_run_all.sh) | Drives every example top-to-bottom (CI-friendly; skips when no SSH host is configured). |

## Running

```bash
pip install -e ".[dev]"
# Set host via env (the cascade) once, then examples need no flags:
export SCITEX_HPC_HOST=spartan
bash examples/00_run_all.sh
```

Outputs land under `examples/_out/<NN_name>/`.
