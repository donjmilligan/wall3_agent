"""Slack mrkdwn -> plain text.

Slack encodes references in angle brackets and escapes three characters
(https://docs.slack.dev/messaging/formatting-message-text):

* ``<@U123>`` / ``<@U123|label>``  user mention     -> ``@Name``
* ``<#C123>`` / ``<#C123|general>`` channel link     -> ``#general``
* ``<!here>``, ``<!channel>``, ``<!everyone>``      -> ``@here`` ...
* ``<!subteam^S123|@team>``                         -> ``@team``
* ``<!date^1700000000^{date}|fallback>``            -> ``fallback``
* ``<https://x|label>``                             -> ``label (https://x)``
* ``<https://x>``                                   -> ``https://x``
* ``<mailto:a@b.example|a@b.example>``              -> ``a@b.example``
* ``&amp;`` ``&lt;`` ``&gt;``                       -> ``&`` ``<`` ``>``
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_TOKEN = re.compile(r"<([^<>]+)>")


def _unescape(text: str) -> str:
    # &amp; last, so "&amp;lt;" (a literal "&lt;" typed by a person) stays "&lt;".
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def render(
    text: str,
    users: Mapping[str, str],
    channels: Mapping[str, str] | None = None,
) -> str:
    """Render ``text`` to plain text.

    ``users`` maps user id -> display name; ``channels`` maps channel id ->
    bare channel name (no ``#``). Unresolvable ids fall back to the label
    Slack supplied, then to the raw id -- never to an empty string.
    """
    channel_names = channels or {}

    def replace(match: re.Match[str]) -> str:
        body = match.group(1)
        target, _, label = body.partition("|")

        if target.startswith("@"):
            user_id = target[1:]
            name = users.get(user_id) or label.lstrip("@") or user_id
            return f"@{name}"

        if target.startswith("#"):
            channel_id = target[1:]
            name = label or channel_names.get(channel_id) or channel_id
            return f"#{name}"

        if target.startswith("!"):
            command = target[1:]
            if command in ("here", "channel", "everyone"):
                return f"@{command}"
            if command.startswith("subteam^"):
                return label or "@group"
            if command.startswith("date^"):
                return label or command
            return label or f"@{command}"

        if target.startswith("mailto:"):
            return label or target[len("mailto:") :]

        if label and label != target:
            return f"{label} ({target})"
        return target

    return _unescape(_TOKEN.sub(replace, text))
