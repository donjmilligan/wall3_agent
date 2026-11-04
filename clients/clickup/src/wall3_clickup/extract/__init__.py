"""Phase 1 -- network only. Calls the client and writes every response verbatim.

Nothing here transforms data: what lands under `.cache/raw/clickup/<UTC ts>/`
is exactly what ClickUp returned, so `normalize/` can be re-run (and tuned)
against a fixed snapshot with zero network access. The one file that is not an
API response is `manifest.json`, which records what was asked for (verb, team,
time window) and anything truncated or skipped.
"""

from .chat import extract_channel
from .inbox import extract_inbox
from .raw_writer import RawWriter
from .task import extract_task

__all__ = ["RawWriter", "extract_channel", "extract_inbox", "extract_task"]
