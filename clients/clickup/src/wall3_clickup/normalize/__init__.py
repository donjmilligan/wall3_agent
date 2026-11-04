"""Phase 2 -- offline. Turns a raw snapshot directory into contract Messages.

Never imports `requests`, the client, extract/ or config (enforced by
tests/test_normalize_boundary.py), so it can be re-run against a fixed
snapshot with zero network access and no credential.
"""

from .messages import NormalizeError, NormalizeResult, normalize_raw_dir
from .tasks import task_matches, task_summary

__all__ = [
    "NormalizeError",
    "NormalizeResult",
    "normalize_raw_dir",
    "task_matches",
    "task_summary",
]
