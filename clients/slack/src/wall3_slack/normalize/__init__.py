"""Phase 2: raw directory -> contract Messages. Never touches the network.

``tests/test_normalize_boundary.py`` fails the build if anything in this
package imports ``requests``, ``wall3_slack.client`` or ``wall3_slack.extract``.
"""

from __future__ import annotations

from pathlib import Path

from ..models import Message
from .messages import build_messages
from .snapshot import NormalizeError, load_snapshot

__all__ = ["NormalizeError", "normalize_dir"]


def normalize_dir(raw_dir: Path) -> list[Message]:
    return build_messages(load_snapshot(raw_dir))
