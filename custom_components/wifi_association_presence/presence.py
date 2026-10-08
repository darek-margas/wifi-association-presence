"""Presence rules: merging AP reads into sightings, grace period, retention, storage.

No Home Assistant imports (only standard library), so these rules can be tested on
their own; the coordinator does the I/O, logging and storage around them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wifi_ap_associations import AccessPointInfo, PollResult

SIGNAL_PERCENT = "%"
SIGNAL_DBM = "dBm"


def signal_quality(signal: int | None, unit: str) -> int | None:
    """A 0-100 quality for comparing signals across drivers (None if unknown).

    Percent is used as is; dBm is mapped linearly, -100 dBm -> 0 and -50 dBm -> 100,
    the usual approximation. Unknown units give None rather than a guess.
    """
    if signal is None:
        return None
    if unit == SIGNAL_PERCENT:
        quality = signal
    elif unit == SIGNAL_DBM:
        quality = 2 * (signal + 100)
    else:
        return None
    return max(0, min(100, quality))


@dataclass(frozen=True, slots=True)
class Sighting:
    """Where and how a MAC was last seen associated."""

    access_point: str
    access_point_id: str  # the access point's subentry id
    ssid: str | None
    band: str | None
    signal: int | None  # on the driver's own scale, see signal_unit
    signal_unit: str
    quality: int | None  # 0-100, comparable across drivers
    last_seen: datetime
    # When the current (or, once away, the last) visit started; it ended at last_seen.
    # Set by carry_visits; None until then.
    arrived: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccessPointState:
    """Latest read of one access point."""

    available: bool
    clients_by_band: dict[str, int] = field(default_factory=dict)
    info: AccessPointInfo | None = None
    last_boot: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccessPointRead:
    """The outcome of polling one access point: a result, or the exception it raised."""

    subentry_id: str
    name: str
    signal_unit: str
    result: PollResult | BaseException


@dataclass(slots=True)
class MergeResult:
    """Sightings from one poll round, states of the readable APs, ids of failed ones."""

    sightings: dict[str, Sighting] = field(default_factory=dict)
    states: dict[str, AccessPointState] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)


def stronger(candidate: Sighting, current: Sighting | None) -> bool:
    """Whether a sighting from another AP in the same poll should replace the current.

    A known quality beats an unknown one; on equal (or both unknown) quality the
    first AP read wins, so attribution doesn't flip between polls.
    """
    if current is None:
        return True
    if candidate.quality is None:
        return False
    return current.quality is None or candidate.quality > current.quality


def merge_reads(reads: Iterable[AccessPointRead], now: datetime) -> MergeResult:
    """Combine one poll round of all access points.

    A failed AP only lands in `failed`; the others still produce sightings. A device
    seen on two APs (roaming) is placed on the one with the stronger signal.
    """
    merged = MergeResult()
    for read in reads:
        if isinstance(read.result, BaseException):
            merged.failed.append(read.subentry_id)
            continue
        per_band: dict[str, int] = {}
        for client in read.result.clients:
            band = client.band or "unknown"
            per_band[band] = per_band.get(band, 0) + 1
            sighting = Sighting(
                access_point=read.name,
                access_point_id=read.subentry_id,
                ssid=client.ssid,
                band=client.band,
                signal=client.signal,
                signal_unit=read.signal_unit,
                quality=signal_quality(client.signal, read.signal_unit),
                last_seen=now,
            )
            if stronger(sighting, merged.sightings.get(client.mac)):
                merged.sightings[client.mac] = sighting
        info = read.result.info
        uptime = info.uptime_seconds if info else None
        merged.states[read.subentry_id] = AccessPointState(
            available=True,
            clients_by_band=per_band,
            info=info,
            # Rounded so the boot time doesn't drift by a second on every poll.
            last_boot=(now - timedelta(seconds=uptime)).replace(second=0, microsecond=0)
            if uptime is not None
            else None,
        )
    return merged


def carry_visits(
    seen: Mapping[str, Sighting],
    previous: Mapping[str, Sighting],
    now: datetime,
    visit_gap: timedelta,
) -> dict[str, Sighting]:
    """Attach the visit's arrival time to this poll's sightings.

    A device seen again within `visit_gap` of its last sighting continues the same
    visit and keeps its arrival time; otherwise a new visit starts now.
    """
    carried: dict[str, Sighting] = {}
    for mac, sighting in seen.items():
        before = previous.get(mac)
        if before is not None and before.arrived is not None and (
            now - before.last_seen <= visit_gap
        ):
            arrived = before.arrived
        else:
            arrived = now
        carried[mac] = replace(sighting, arrived=arrived)
    return carried


def is_present(sighting: Sighting | None, now: datetime, consider_home: timedelta) -> bool:
    """Seen within the grace period."""
    return sighting is not None and now - sighting.last_seen < consider_home


def ride_out_total_failure(
    last_success: datetime | None, now: datetime, consider_home: timedelta
) -> bool:
    """Whether a poll in which no access point could be read may keep the old sightings.

    Only while the last successful poll is within the grace period: until then every
    device seen in it still counts as home, as with a single failing AP. Past it, the
    devices would turn away for no reason other than the outage, so presence must
    become unavailable instead. Never before the first successful poll.
    """
    return last_success is not None and now - last_success < consider_home


def prune(
    sightings: Mapping[str, Sighting], now: datetime, retention: timedelta
) -> dict[str, Sighting]:
    """Drop sightings older than the retention period."""
    cutoff = now - retention
    return {mac: s for mac, s in sightings.items() if s.last_seen > cutoff}


_TIME_FIELDS = ("last_seen", "arrived")


def sightings_to_storage(
    sightings: Mapping[str, Sighting],
    saved_at: datetime | None = None,
    clean: bool = False,
) -> dict[str, Any]:
    """JSON-friendly form of the sightings, stamped with when they were written.

    `clean` marks the write made as Home Assistant stops (or the entry unloads): the
    sightings are current up to `saved_at`. A periodic write is not clean: devices may
    have come and gone after it, before an unclean shutdown (crash, power cut).
    """
    data: dict[str, Any] = {
        "sightings": {
            mac: {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in asdict(s).items()
            }
            for mac, s in sightings.items()
        }
    }
    if saved_at is not None:
        data["saved_at"] = saved_at.isoformat()
        data["clean"] = clean
    return data


def stored_at(stored: Mapping[str, Any] | None) -> datetime | None:
    """When stored sightings were written; None if unknown (files before 0.5.0)."""
    try:
        return _parse_time((stored or {}).get("saved_at"))
    except (TypeError, ValueError):
        return None


def stored_cleanly(stored: Mapping[str, Any] | None) -> bool:
    """Whether the sightings were written at stop or unload, so current until then.

    Files from 0.5.0 and 0.5.1 have no flag; their last write was the one at stop
    (or unload) in practice, so they count as clean, which keeps visits across the
    upgrade restart.
    """
    return (stored or {}).get("clean", True) is True


def restart_visit_gap(
    visit_gap: timedelta,
    saved_at: datetime | None,
    clean: bool,
    now: datetime,
) -> timedelta:
    """The visit gap for the first poll after the sightings were restored.

    After a clean save, Home Assistant being down between the save and now is no
    evidence of anyone leaving: the downtime is added to the gap, so a device keeps its
    visit exactly when it was within the gap at the save (the downtime cancels out).
    After an unclean shutdown the sightings may be up to a save interval old, and a
    device may have left after that write; extending the gap could then join visits
    across the outage and hide a departure, so the normal gap applies and a device seen
    again starts a new visit.
    """
    if saved_at is None or not clean:
        return visit_gap
    return visit_gap + max(now - saved_at, timedelta(0))


def _parse_time(value: Any) -> datetime | None:
    """An aware datetime from ISO text; None for None; ValueError for anything else."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("naive datetime")
    return parsed


def sightings_from_storage(
    stored: Mapping[str, Any] | None, now: datetime, retention: timedelta
) -> dict[str, Sighting]:
    """Read stored sightings, skipping malformed and expired entries.

    Entries written by 0.2.x (with "rssi", always percent) are converted; entries
    written before visits were tracked start their visit at their last sighting, and
    the "departed" field of 0.4.0 is dropped.
    """
    sightings: dict[str, Sighting] = {}
    items = (stored or {}).get("sightings")
    if not isinstance(items, Mapping):
        return sightings
    for mac, item in items.items():
        try:
            item = dict(item)
            if "rssi" in item:
                item["signal"] = item.pop("rssi")
            item.pop("departed", None)
            item.setdefault("signal_unit", SIGNAL_PERCENT)
            item.setdefault("quality", signal_quality(item.get("signal"), item["signal_unit"]))
            for key in _TIME_FIELDS:
                item[key] = _parse_time(item.get(key))
            if item["last_seen"] is None:
                continue
            if item["arrived"] is None:
                item["arrived"] = item["last_seen"]
            sighting = Sighting(**item)
        except (TypeError, KeyError, ValueError):
            continue
        sightings[mac] = sighting
    return prune(sightings, now, retention)
