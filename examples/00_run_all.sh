#!/usr/bin/env bash
# Run every example top-to-bottom. Outputs land under examples/_out/.
# Examples that need a real SSH host check $SCITEX_HPC_HOST and skip
# cleanly if unset, so this is CI-friendly.
set -euo pipefail

cd "$(dirname "$0")"

# Glob over numbered examples so adding a new one needs no edit here.
shopt -s nullglob
for ex in [0-9][0-9]_*.py; do
    echo "── $ex ────────────────────────────────────────"
    python "$ex"
done

echo "── done ──"
