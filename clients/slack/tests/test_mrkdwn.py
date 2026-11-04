from __future__ import annotations

import pytest

from wall3_slack.normalize.mrkdwn import render

USERS = {"U000ALICE": "Alice Example", "U000BOB": "Bob Example"}
CHANNELS = {"C000GENERAL": "general"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hi <@U000ALICE>", "hi @Alice Example"),
        ("hi <@U000ALICE|alice>", "hi @Alice Example"),
        ("hi <@U000GONE|gone>", "hi @gone"),
        ("hi <@U000GONE>", "hi @U000GONE"),
        ("see <#C000GENERAL>", "see #general"),
        ("see <#C000OTHER|random>", "see #random"),
        ("<!here> standup", "@here standup"),
        ("<!channel>", "@channel"),
        ("<!subteam^S000TEAM|@oncall> help", "@oncall help"),
        ("due <!date^1793793600^{date_short}|Nov 4>", "due Nov 4"),
        ("<https://example.com/plan|the plan>", "the plan (https://example.com/plan)"),
        ("<https://example.com/plan>", "https://example.com/plan"),
        ("<https://example.com|https://example.com>", "https://example.com"),
        ("<mailto:a@example.com|a@example.com>", "a@example.com"),
        ("a &lt; b &amp;&amp; c &gt; d", "a < b && c > d"),
        ("literal &amp;lt;", "literal &lt;"),
        ("<https://example.com/?a=1&amp;b=2|q>", "q (https://example.com/?a=1&b=2)"),
        ("no markup at all", "no markup at all"),
        ("<@U000ALICE> and <@U000BOB>", "@Alice Example and @Bob Example"),
    ],
)
def test_render(raw, expected):
    assert render(raw, USERS, CHANNELS) == expected
