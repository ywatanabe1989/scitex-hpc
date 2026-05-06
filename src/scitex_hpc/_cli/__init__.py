"""scitex-hpc CLI package.

Re-exports ``main`` and the click ``cli`` group so existing imports
``from scitex_hpc._cli import main`` continue to work.
"""

from .._reservation import Reservation  # re-export for legacy tests
from ._root import cli, main

__all__ = ["Reservation", "cli", "main"]
