"""Coverage: how well the Wi-Fi covers the home, from what the access points report.

Counted from the polls the integration makes anyway, with no Home Assistant imports so
it is tested on its own (like presence.py):

- per device, per day: **roams** (it moved to another access point), **short drops**
  (it vanished from every access point and came back within the grace period, which
  presence hides; frequent ones point to a coverage hole or aggressive power saving)
  and **late roams** (its signal jumped by about 15 dB right after switching: it stayed
  too long on a far access point, a "sticky" client);
- per access point: the **average signal** of its clients and how many are **weak**.

Signal is compared as quality (0-100, the same for every driver: -100 dBm is 0,
-50 dBm is 100). Counts live in memory and start again at local midnight and after a
restart.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .presence import Sighting

# Quality below this is a weak client: 50 is about -75 dBm.
WEAK_QUALITY = 50
# A switch that gains this much quality was overdue: 30 points is about 15 dB.
LATE_ROAM_GAIN = 30


@dataclass(slots=True)
class DeviceCounts:
    """Today's coverage events of one device."""

    roams: int = 0
    drops: int = 0
    late_roams: int = 0


@dataclass(frozen=True, slots=True)
class AccessPointCoverage:
    """Signal of the clients connected to one access point in the latest poll."""

    clients: int
    average_quality: int | None
    weak_clients: int


@dataclass(slots=True)
class CoverageTracker:
    """Counts roams, short drops and late roams per device and day."""

    day: date | None = None
    counts: dict[str, DeviceCounts] = field(default_factory=dict)
    # Per MAC: the access point, quality and time of the last poll it was seen in.
    _last: dict[str, tuple[str, int | None, datetime]] = field(default_factory=dict)
    # MACs missing from every access point since then (not yet gone).
    _missing: set[str] = field(default_factory=set)

    def reset_day(self, today: date) -> None:
        """Reset daily counts independently of whether access points can be read."""
        if today != self.day:
            self.day = today
            self.counts = {}

    def _forget(self, mac: str) -> None:
        """Drop what is known about a device: its next sighting starts afresh."""
        self._last.pop(mac, None)
        self._missing.discard(mac)

    def update(
        self,
        seen: Mapping[str, Sighting],
        failed_access_points: Collection[str],
        now: datetime,
        today: date,
        grace: timedelta,
    ) -> None:
        """Feed one poll: who was seen where.

        A gap is measured from when the device was last seen, as presence does: shorter
        than the grace period it was a drop (the tracker stayed home), otherwise the
        device left, which is neither a drop nor, on its return, a roam.
        """
        self.reset_day(today)
        for mac, (access_point_id, _quality, last_seen) in list(self._last.items()):
            # An unreadable AP interrupts observation: neither a gap nor a roam across
            # that interval can be established from these polls.
            if access_point_id in failed_access_points or now - last_seen >= grace:
                self._forget(mac)
        for mac, sighting in seen.items():
            counts = self.counts.setdefault(mac, DeviceCounts())
            if mac in self._missing:
                counts.drops += 1
            previous = self._last.get(mac)
            if previous is not None and previous[0] != sighting.access_point_id:
                counts.roams += 1
                old_quality, new_quality = previous[1], sighting.quality
                if (
                    old_quality is not None
                    and new_quality is not None
                    and new_quality - old_quality >= LATE_ROAM_GAIN
                ):
                    counts.late_roams += 1
            self._last[mac] = (sighting.access_point_id, sighting.quality, now)
            self._missing.discard(mac)
        self._missing.update(mac for mac in self._last if mac not in seen)


def access_point_coverage(seen: Mapping[str, Sighting]) -> dict[str, AccessPointCoverage]:
    """Average and weak client signal per access point, from one poll's sightings."""
    qualities: dict[str, list[int]] = {}
    clients: dict[str, int] = {}
    for sighting in seen.values():
        clients[sighting.access_point_id] = clients.get(sighting.access_point_id, 0) + 1
        if sighting.quality is not None:
            qualities.setdefault(sighting.access_point_id, []).append(sighting.quality)
    return {
        access_point_id: AccessPointCoverage(
            clients=count,
            average_quality=(
                round(sum(values) / len(values))
                if (values := qualities.get(access_point_id))
                else None
            ),
            weak_clients=sum(1 for q in qualities.get(access_point_id, []) if q < WEAK_QUALITY),
        )
        for access_point_id, count in clients.items()
    }
