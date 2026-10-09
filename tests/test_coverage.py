"""Tests for the coverage counts (roams, short drops, late roams) and per-AP signal.

Run with: python3 -m pytest tests (needs pytest and asyncssh installed).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "custom_components" / "wifi_association_presence"),
)

from coverage_stats import (  # noqa: E402
    AccessPointCoverage,
    CoverageTracker,
    access_point_coverage,
)
from presence import SIGNAL_PERCENT, Sighting  # noqa: E402

START = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 10, 9)
GRACE = timedelta(minutes=3)
POLL = timedelta(seconds=30)
PHONE = "5C:AD:BA:00:00:01"
CAR = "4C:FC:AA:00:00:02"


def seen(ap: str, quality: int | None = 70) -> Sighting:
    return Sighting(ap, ap, "Home", "5GHz", quality, SIGNAL_PERCENT, quality, START)


def feed(tracker: CoverageTracker, polls, *, failed=(), today: date = TODAY) -> datetime:
    """Feed polls (dicts of MAC -> Sighting) 30 s apart; returns the time of the last."""
    now = START
    for number, poll in enumerate(polls):
        now = START + number * POLL
        tracker.update(poll, failed, now, today, GRACE)
    return now


def test_staying_on_one_access_point_counts_nothing() -> None:
    tracker = CoverageTracker()
    feed(tracker, [{PHONE: seen("hall")}] * 5)
    assert tracker.counts[PHONE].roams == 0
    assert tracker.counts[PHONE].drops == 0


def test_moving_to_another_access_point_is_a_roam() -> None:
    tracker = CoverageTracker()
    feed(tracker, [{PHONE: seen("hall")}, {PHONE: seen("kitchen")}, {PHONE: seen("hall")}])
    assert tracker.counts[PHONE].roams == 2
    assert tracker.counts[PHONE].late_roams == 0


def test_big_signal_gain_after_a_roam_is_a_late_roam() -> None:
    tracker = CoverageTracker()
    feed(tracker, [{PHONE: seen("hall", 30)}, {PHONE: seen("kitchen", 75)}])
    assert tracker.counts[PHONE].roams == 1
    assert tracker.counts[PHONE].late_roams == 1


def test_unknown_signal_is_not_a_late_roam() -> None:
    tracker = CoverageTracker()
    feed(tracker, [{PHONE: seen("hall", None)}, {PHONE: seen("kitchen", 90)}])
    assert tracker.counts[PHONE].late_roams == 0


def test_short_disappearance_is_a_drop() -> None:
    tracker = CoverageTracker()
    feed(tracker, [{PHONE: seen("hall")}, {}, {}, {PHONE: seen("hall")}])
    assert tracker.counts[PHONE].drops == 1
    assert tracker.counts[PHONE].roams == 0


def test_leaving_for_longer_than_the_grace_period_is_not_a_drop() -> None:
    tracker = CoverageTracker()
    away = [{}] * 10  # 5 minutes, longer than the 3 minute grace period
    feed(tracker, [{PHONE: seen("hall")}, *away, {PHONE: seen("kitchen")}])
    assert tracker.counts[PHONE].drops == 0
    # Coming home at another access point isn't a roam either.
    assert tracker.counts[PHONE].roams == 0


def test_failed_access_point_does_not_make_its_clients_drop() -> None:
    tracker = CoverageTracker()
    tracker.update({PHONE: seen("hall")}, (), START, TODAY, GRACE)
    tracker.update({}, ("hall",), START + POLL, TODAY, GRACE)
    tracker.update({PHONE: seen("hall")}, (), START + 2 * POLL, TODAY, GRACE)
    assert tracker.counts[PHONE].drops == 0


def test_counts_are_per_device() -> None:
    tracker = CoverageTracker()
    feed(
        tracker,
        [
            {PHONE: seen("hall"), CAR: seen("garage")},
            {PHONE: seen("kitchen"), CAR: seen("garage")},
        ],
    )
    assert tracker.counts[PHONE].roams == 1
    assert tracker.counts[CAR].roams == 0


def test_counts_start_again_on_a_new_day() -> None:
    tracker = CoverageTracker()
    feed(tracker, [{PHONE: seen("hall")}, {PHONE: seen("kitchen")}])
    assert tracker.counts[PHONE].roams == 1
    tracker.update({PHONE: seen("hall")}, (), START + timedelta(hours=12), date(2026, 10, 10), GRACE)
    assert tracker.day == date(2026, 10, 10)
    # The roam that happened across midnight counts for the new day.
    assert tracker.counts[PHONE].roams == 1
    tracker.update({PHONE: seen("hall")}, (), START + timedelta(hours=13), date(2026, 10, 10), GRACE)
    assert tracker.counts[PHONE].roams == 1


def test_access_point_coverage_average_and_weak_clients() -> None:
    coverage = access_point_coverage(
        {
            PHONE: seen("hall", 80),
            CAR: seen("hall", 40),
            "AA:00:00:00:00:03": seen("hall", None),
            "AA:00:00:00:00:04": seen("kitchen", 49),
        }
    )
    assert coverage["hall"] == AccessPointCoverage(clients=3, average_quality=60, weak_clients=1)
    assert coverage["kitchen"] == AccessPointCoverage(clients=1, average_quality=49, weak_clients=1)


def test_access_point_coverage_without_signal() -> None:
    coverage = access_point_coverage({PHONE: seen("hall", None)})
    assert coverage["hall"] == AccessPointCoverage(clients=1, average_quality=None, weak_clients=0)
    assert access_point_coverage({}) == {}
