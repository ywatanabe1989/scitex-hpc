#!/usr/bin/env python3
"""Book a persistent SLURM reservation programmatically.

Equivalent to:

    scitex-hpc reservations book sample-pool --time 1:00:00 --persistent
"""

from __future__ import annotations

import os

import scitex as stx


@stx.session
def main(
    name: str = "sample-pool",
    time: str = "1:00:00",
    cpus: int = 2,
    mem: str = "4G",
    persistent: bool = False,
    logger=stx.INJECTED,
):
    """Book a reservation; print + save the lease metadata."""
    if not os.environ.get("SCITEX_HPC_HOST"):
        logger.warning(
            "SCITEX_HPC_HOST not set — cannot reach a cluster. "
            "Set it (or write `host: <cluster>` into ~/.scitex/hpc/config.yaml) "
            "and re-run."
        )
        return 0

    from scitex_hpc._config import JobConfig
    from scitex_hpc._reservation import Reservation

    cfg = JobConfig(
        project=name,
        cpus=cpus,
        time=time,
        mem=mem,
        job_name=name,
    )
    res = Reservation.book(cfg, persistent=persistent)
    payload = {
        "id": res.id,
        "job_id": res.job_id,
        "node": res.node,
        "host": res.host,
        "name": res.name,
    }
    logger.info(f"booked: {payload}")
    stx.io.save(payload, "reservation.json")
    return 0


if __name__ == "__main__":
    main()
