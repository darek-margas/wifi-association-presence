"""Presence rules: merging AP reads into sightings, grace period, retention, storage.

No Home Assistant imports (only standard library), so these rules can be tested on
their own; the coordinator does the I/O, logging and storage around them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ap_drivers import AccessPointInfo, PollResult

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


def is_present(sighting: Sighting | None, now: datetime, consider_home: timedelta) -> bool:
    """Seen within the grace period."""
    return sighting is not None and now - sighting.last_seen < consider_home


def prune(
    sightings: Mapping[str, Sighting], now: datetime, retention: timedelta
) -> dict[str, Sighting]:
    """Drop sightings older than the retention period."""
    cutoff = now - retention
    return {mac: s for mac, s in sightings.items() if s.last_seen > cutoff}


def sightings_to_storage(sightings: Mapping[str, Sighting]) -> dict[str, Any]:
    """JSON-friendly form of the sightings."""
    return {
        "sightings": {
            mac: {**asdict(s), "last_seen": s.last_seen.isoformat()}
            for mac, s in sightings.items()
        }
    }


def sightings_from_storage(
    stored: Mapping[str, Any] | None, now: datetime, retention: timedelta
) -> dict[str, Sighting]:
    """Read stored sightings, skipping malformed and expired entries.

    Entries written by 0.2.x (with "rssi", always percent) are converted.
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
                item.setdefault("signal_unit", SIGNAL_PERCENT)
            item.setdefault("signal_unit", SIGNAL_PERCENT)
            item.setdefault("quality", signal_quality(item.get("signal"), item["signal_unit"]))
            last_seen = datetime.fromisoformat(item.pop("last_seen"))
            if last_seen.tzinfo is None:
                continue
            sighting = Sighting(**item, last_seen=last_seen)
        except (TypeError, KeyError, ValueError):
            continue
        sightings[mac] = sighting
    return prune(sightings, now, retention)
