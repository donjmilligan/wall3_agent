from __future__ import annotations

import pytest

from wall3_slack.normalize.fields import (
    build_permalink,
    mpim_readable_name,
    thread_ts_from_permalink,
    ts_key,
    ts_to_iso,
    user_display_name,
)


@pytest.mark.parametrize(
    ("ts", "iso"),
    [
        ("0.000000", "1970-01-01T00:00:00Z"),
        ("1793782800.000100", "2026-11-04T09:00:00Z"),
        ("1793799000.999999", "2026-11-04T13:30:00Z"),  # truncated, not rounded up
        ("1358546515.000008", "2013-01-18T22:01:55Z"),
    ],
)
def test_ts_to_iso(ts, iso):
    assert ts_to_iso(ts) == iso


def test_ts_key_is_exact_at_microsecond_resolution():
    assert ts_key("1793782800.000100") < ts_key("1793782800.000101")
    assert ts_key("1793782800.1") == (1793782800, 100000)


def test_permalink_top_level_matches_documented_format():
    # docs.slack.dev chat.getPermalink example: .../archives/C1H9RESGA/p135854651500008
    assert (
        build_permalink("https://ghostbusters.slack.com/", "C1H9RESGA", "1358546515.00008", None)
        == "https://ghostbusters.slack.com/archives/C1H9RESGA/p135854651500008"
    )


def test_permalink_reply_carries_thread_query():
    assert build_permalink(
        "https://example.slack.com", "C000GENERAL", "1793797200.000100", "1793718000.000100"
    ) == (
        "https://example.slack.com/archives/C000GENERAL/p1793797200000100"
        "?thread_ts=1793718000.000100&cid=C000GENERAL"
    )


def test_permalink_parent_has_no_thread_query():
    link = build_permalink("https://example.slack.com/", "C1", "1.000001", "1.000001")
    assert link == "https://example.slack.com/archives/C1/p1000001"


def test_thread_ts_from_permalink():
    assert (
        thread_ts_from_permalink(
            "https://example.slack.com/archives/C1/p1000002?thread_ts=1.000001&cid=C1"
        )
        == "1.000001"
    )
    assert thread_ts_from_permalink("https://example.slack.com/archives/C1/p1000002") is None


def test_display_name_preference():
    assert (
        user_display_name(
            {"id": "U1", "name": "al", "real_name": "Al Real", "profile": {"display_name": "Al"}}
        )
        == "Al"
    )
    assert (
        user_display_name(
            {"id": "U1", "name": "al", "profile": {"display_name": "", "real_name": "Al Real"}}
        )
        == "Al Real"
    )
    assert user_display_name({"id": "U1", "name": "al", "profile": {}}) == "al"
    assert user_display_name({"id": "U1"}) == "U1"


def test_mpim_name():
    assert mpim_readable_name("mpdm-alice--bob--don-1") == "alice, bob, don"
    assert mpim_readable_name("project-x") == "project-x"
