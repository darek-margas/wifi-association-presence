"""Tests for the presence rules (merging AP reads, grace period, retention, storage).

Run with: python3 -m pytest tests (needs pytest and asyncssh installed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "custom_components" / "wifi_association_presence"),
)

from ap_drivers import AccessPointError, AccessPointInfo, AssociatedClient, PollResult  # noqa: E402
from presence import (  # noqa: E402
    SIGNAL_DBM,
    SIGNAL_PERCENT,
    AccessPointRead,
    Sighting,
    carry_visits,
    is_present,
    merge_reads,
    prune,
    ride_out_total_failure,
    signal_quality,
    sightings_from_storage,
    sightings_to_storage,
    stronger,
)

NOW = datetime(2026, 10, 7, 12, 0, 30, tzinfo=UTC)
PHONE = "5C:AD:BA:00:00:01"
CAR = "4C:FC:AA:00:00:02"


def read(
    ap: str,
    *clients: AssociatedClient,
    unit: str = SIGNAL_PERCENT,
    info: AccessPointInfo | None = None,
) -> AccessPointRead:
    return AccessPointRead(ap, ap.title(), unit, PollResult(list(clients), info))


def failed(ap: str) -> AccessPointRead:
    return AccessPointRead(ap, ap.title(), SIGNAL_PERCENT, AccessPointError("timeout"))


def client(mac: str, signal: int | None, band: str = "5GHz") -> AssociatedClient:
    return AssociatedClient(mac=mac, ssid="Home", band=band, signal=signal)


def sighting(
    last_seen: datetime,
    signal: int | None = 80,
    arrived: datetime | None = None,
) -> Sighting:
    return Sighting(
        "Studio", "studio", "Home", "5GHz", signal, SIGNAL_PERCENT, signal, last_seen, arrived
    )


# --- signal scale ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("signal", "unit", "expected"),
    [
        (98, SIGNAL_PERCENT, 98),
        (130, SIGNAL_PERCENT, 100),
        (-5, SIGNAL_PERCENT, 0),
        (-50, SIGNAL_DBM, 100),
        (-67, SIGNAL_DBM, 66),
        (-100, SIGNAL_DBM, 0),
        (-110, SIGNAL_DBM, 0),
        (None, SIGNAL_PERCENT, None),
        (None, SIGNAL_DBM, None),
        (50, "bars", None),
    ],
)
def test_signal_quality(signal: int | None, unit: str, expected: int | None) -> None:
    assert signal_quality(signal, unit) == expected


# --- merging one poll round -----------------------------------------------------


def test_one_ap_failing_does_not_stop_the_others() -> None:
    merged = merge_reads([failed("hall"), read("studio", client(PHONE, 90))], NOW)
    assert merged.failed == ["hall"]
    assert merged.sightings[PHONE].access_point_id == "studio"
    assert set(merged.states) == {"studio"}


def test_all_aps_failing_reports_every_failure() -> None:
    merged = merge_reads([failed("hall"), failed("studio")], NOW)
    assert merged.failed == ["hall", "studio"]
    assert merged.sightings == {}
    assert merged.states == {}


@pytest.mark.parametrize("order", [("hall", "studio"), ("studio", "hall")])
def test_roaming_device_goes_to_the_stronger_signal(order: tuple[str, str]) -> None:
    signals = {"hall": 40, "studio": 90}
    merged = merge_reads([read(ap, client(PHONE, signals[ap])) for ap in order], NOW)
    assert merged.sightings[PHONE].access_point_id == "studio"


def test_unknown_signal_never_beats_a_known_one() -> None:
    merged = merge_reads(
        [read("hall", client(PHONE, None)), read("studio", client(PHONE, 1))], NOW
    )
    assert merged.sightings[PHONE].access_point_id == "studio"
    merged = merge_reads(
        [read("studio", client(PHONE, 1)), read("hall", client(PHONE, None))], NOW
    )
    assert merged.sightings[PHONE].access_point_id == "studio"


def test_equal_signal_keeps_the_first_ap() -> None:
    merged = merge_reads(
        [read("hall", client(PHONE, 70)), read("studio", client(PHONE, 70))], NOW
    )
    assert merged.sightings[PHONE].access_point_id == "hall"
    merged = merge_reads(
        [read("hall", client(PHONE, None)), read("studio", client(PHONE, None))], NOW
    )
    assert merged.sightings[PHONE].access_point_id == "hall"


def test_dbm_and_percent_drivers_are_compared_on_quality() -> None:
    # -50 dBm is an excellent signal (quality 100) and must beat 80 %;
    # comparing the raw numbers would have picked 80 > -50.
    merged = merge_reads(
        [
            read("hall", client(PHONE, 80)),
            read("garden", client(PHONE, -50), unit=SIGNAL_DBM),
        ],
        NOW,
    )
    best = merged.sightings[PHONE]
    assert best.access_point_id == "garden"
    assert (best.signal, best.signal_unit, best.quality) == (-50, SIGNAL_DBM, 100)


def test_stronger_rules() -> None:
    known = sighting(NOW, 50)
    unknown = sighting(NOW, None)
    assert stronger(known, None)
    assert stronger(unknown, None)
    assert stronger(known, unknown)
    assert not stronger(unknown, known)
    assert not stronger(known, known)


def test_access_point_state_counts_bands_and_rounds_boot_time() -> None:
    merged = merge_reads(
        [
            read(
                "studio",
                client(PHONE, 90, band="5GHz"),
                client(CAR, 60, band="2.4GHz"),
                client("00:11:22:33:44:55", 70, band="2.4GHz"),
                info=AccessPointInfo(name="Studio", uptime_seconds=3600),
            )
        ],
        NOW,
    )
    state = merged.states["studio"]
    assert state.available
    assert state.clients_by_band == {"5GHz": 1, "2.4GHz": 2}
    # 12:00:30 minus one hour, rounded down to the minute.
    assert state.last_boot == datetime(2026, 10, 7, 11, 0, tzinfo=UTC)


def test_access_point_without_info_has_no_boot_time() -> None:
    merged = merge_reads([read("studio", client(PHONE, 90))], NOW)
    assert merged.states["studio"].last_boot is None


# --- grace period and retention -------------------------------------------------


def test_grace_period() -> None:
    grace = timedelta(minutes=3)
    assert is_present(sighting(NOW), NOW, grace)
    assert is_present(sighting(NOW - grace + timedelta(seconds=1)), NOW, grace)
    assert not is_present(sighting(NOW - grace), NOW, grace)
    assert not is_present(None, NOW, grace)


def test_zero_grace_period_means_away_once_missed() -> None:
    assert not is_present(sighting(NOW - timedelta(seconds=1)), NOW, timedelta(0))


def test_prune_drops_old_sightings() -> None:
    week = timedelta(days=7)
    kept = prune(
        {PHONE: sighting(NOW - timedelta(days=6)), CAR: sighting(NOW - timedelta(days=8))},
        NOW,
        week,
    )
    assert set(kept) == {PHONE}


# --- storage --------------------------------------------------------------------


def test_storage_round_trip() -> None:
    original = {
        PHONE: sighting(
            NOW - timedelta(hours=1),
            arrived=NOW - timedelta(hours=3),
        ),
        CAR: Sighting("Garden", "garden", None, None, -67, SIGNAL_DBM, 66, NOW, NOW),
    }
    assert sightings_from_storage(sightings_to_storage(original), NOW, timedelta(days=7)) == original


def test_storage_converts_0_2_entries() -> None:
    stored = {
        "sightings": {
            PHONE: {
                "access_point": "Studio",
                "access_point_id": "studio",
                "ssid": "Home",
                "band": "5GHz",
                "rssi": 98,
                "last_seen": NOW.isoformat(),
            }
        }
    }
    restored = sightings_from_storage(stored, NOW, timedelta(days=7))[PHONE]
    assert (restored.signal, restored.signal_unit, restored.quality) == (98, SIGNAL_PERCENT, 98)
    # No visit was recorded then: it starts at the last sighting.
    assert restored.arrived == NOW


def test_storage_skips_bad_and_expired_entries() -> None:
    good = sightings_to_storage({PHONE: sighting(NOW, arrived=NOW)})["sightings"][PHONE]
    stored = {
        "sightings": {
            PHONE: good,
            "AA:00:00:00:00:01": {**good, "last_seen": "not a date"},
            "AA:00:00:00:00:02": {**good, "last_seen": "2026-10-07T12:00:00"},  # no tz
            "AA:00:00:00:00:03": {k: v for k, v in good.items() if k != "access_point"},
            "AA:00:00:00:00:04": {**good, "unexpected": 1},
            "AA:00:00:00:00:05": "garbage",
            "AA:00:00:00:00:06": {**good, "last_seen": (NOW - timedelta(days=8)).isoformat()},
            "AA:00:00:00:00:07": {**good, "arrived": "yesterday"},
        }
    }
    assert set(sightings_from_storage(stored, NOW, timedelta(days=7))) == {PHONE}


@pytest.mark.parametrize("stored", [None, {}, {"sightings": None}, {"sightings": []}])
def test_storage_empty_or_broken_file(stored: object) -> None:
    assert sightings_from_storage(stored, NOW, timedelta(days=7)) == {}  # type: ignore[arg-type]


# --- visits ---------------------------------------------------------------------

GAP = timedelta(minutes=3)


def test_first_sighting_starts_a_visit() -> None:
    seen = carry_visits({PHONE: sighting(NOW)}, {}, NOW, GAP)
    assert seen[PHONE].arrived == NOW


def test_seen_again_within_the_gap_continues_the_visit() -> None:
    arrived = NOW - timedelta(hours=2)
    before = {PHONE: sighting(NOW - timedelta(minutes=1), arrived=arrived)}
    seen = carry_visits({PHONE: sighting(NOW)}, before, NOW, GAP)
    assert seen[PHONE].arrived == arrived
    assert seen[PHONE].last_seen == NOW


def test_gap_at_the_limit_still_continues() -> None:
    before = {PHONE: sighting(NOW - GAP, arrived=NOW - timedelta(hours=1))}
    seen = carry_visits({PHONE: sighting(NOW)}, before, NOW, GAP)
    assert seen[PHONE].arrived == NOW - timedelta(hours=1)


def test_return_after_the_gap_starts_a_new_visit() -> None:
    before = {PHONE: sighting(NOW - timedelta(hours=4), arrived=NOW - timedelta(hours=8))}
    seen = carry_visits({PHONE: sighting(NOW)}, before, NOW, GAP)
    assert seen[PHONE].arrived == NOW


def test_devices_not_seen_this_poll_are_untouched() -> None:
    before = {CAR: sighting(NOW - timedelta(hours=1), arrived=NOW - timedelta(hours=2))}
    seen = carry_visits({PHONE: sighting(NOW)}, before, NOW, GAP)
    assert set(seen) == {PHONE}


def test_storage_drops_the_0_4_0_departed_field() -> None:
    entry = sightings_to_storage({PHONE: sighting(NOW, arrived=NOW)})["sightings"][PHONE]
    stored = {"sightings": {PHONE: {**entry, "departed": NOW.isoformat()}}}
    assert sightings_from_storage(stored, NOW, timedelta(days=7)) == {
        PHONE: sighting(NOW, arrived=NOW)
    }


# --- total failure --------------------------------------------------------------


def test_total_failure_is_ridden_out_only_within_grace() -> None:
    grace = timedelta(minutes=3)
    assert ride_out_total_failure(NOW - timedelta(seconds=61), NOW, grace)
    assert ride_out_total_failure(NOW - grace + timedelta(seconds=1), NOW, grace)
    assert not ride_out_total_failure(NOW - grace, NOW, grace)
    # Short grace: the very next poll is already past it, so no false "away".
    assert not ride_out_total_failure(NOW - timedelta(seconds=61), NOW, timedelta(seconds=30))


def test_total_failure_before_any_success_is_not_ridden_out() -> None:
    assert not ride_out_total_failure(None, NOW, timedelta(minutes=3))
