"""Poll every configured access point and remember where each MAC was last seen."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .ap_drivers import DRIVERS, AccessPointDriver, AccessPointError, AccessPointInfo
from .const import (
    CONF_CONSIDER_HOME,
    CONF_DRIVER,
    DEFAULT_CONSIDER_HOME,
    DOMAIN,
    LOGGER,
    SCAN_INTERVAL,
    SUBENTRY_ACCESS_POINT,
)

type WifiAssociationConfigEntry = ConfigEntry[AssociationCoordinator]


@dataclass(frozen=True, slots=True)
class Sighting:
    """Where and how a MAC was last seen associated."""

    access_point: str
    ssid: str | None
    band: str | None
    rssi: int | None
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class AccessPointState:
    """Latest read of one access point."""

    available: bool
    clients_by_band: dict[str, int] = field(default_factory=dict)
    info: AccessPointInfo | None = None
    last_boot: datetime | None = None


@dataclass(frozen=True, slots=True)
class PresenceData:
    """Coordinator data: sightings by MAC and access point state by subentry id."""

    sightings: dict[str, Sighting]
    access_points: dict[str, AccessPointState]


@dataclass(frozen=True, slots=True)
class ConfiguredAccessPoint:
    """An access point subentry and its driver."""

    subentry_id: str
    title: str
    driver: AccessPointDriver


class AssociationCoordinator(DataUpdateCoordinator[PresenceData]):
    """Merge the association tables of all access points."""

    config_entry: WifiAssociationConfigEntry

    def __init__(self, hass: HomeAssistant, entry: WifiAssociationConfigEntry) -> None:
        """Create one driver per access point subentry."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.access_points: list[ConfiguredAccessPoint] = []
        for subentry in entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_ACCESS_POINT:
                continue
            driver_cls = DRIVERS.get(subentry.data[CONF_DRIVER])
            if driver_cls is None:
                LOGGER.error(
                    "Access point %s uses unknown type %s; skipping it",
                    subentry.title,
                    subentry.data[CONF_DRIVER],
                )
                continue
            self.access_points.append(
                ConfiguredAccessPoint(
                    subentry.subentry_id, subentry.title, driver_cls(dict(subentry.data))
                )
            )
        self._sightings: dict[str, Sighting] = {}
        self._ap_states: dict[str, AccessPointState] = {}
        self._failing: set[str] = set()
        self.consider_home = timedelta(
            seconds=entry.options.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)
        )

    @property
    def has_access_points(self) -> bool:
        """Whether any access point is configured (else presence is unknown)."""
        return bool(self.access_points)

    async def _async_update_data(self) -> PresenceData:
        """Read every AP in parallel; keep previous sightings for APs that fail."""
        if not self.access_points:
            return PresenceData({}, {})

        results = await asyncio.gather(
            *(ap.driver.async_poll() for ap in self.access_points),
            return_exceptions=True,
        )
        now = dt_util.utcnow()
        best: dict[str, Sighting] = {}
        failed = 0
        for ap, result in zip(self.access_points, results, strict=True):
            if isinstance(result, AccessPointError):
                failed += 1
                if ap.title not in self._failing:
                    LOGGER.warning("Cannot read access point %s: %s", ap.title, result)
                    self._failing.add(ap.title)
                previous = self._ap_states.get(ap.subentry_id)
                self._ap_states[ap.subentry_id] = AccessPointState(
                    available=False, info=previous.info if previous else None
                )
                continue
            if isinstance(result, BaseException):
                raise result
            if ap.title in self._failing:
                LOGGER.info("Access point %s is readable again", ap.title)
                self._failing.discard(ap.title)

            per_band: dict[str, int] = {}
            for client in result.clients:
                per_band[client.band or "unknown"] = per_band.get(client.band or "unknown", 0) + 1
                sighting = Sighting(ap.title, client.ssid, client.band, client.rssi, now)
                current = best.get(client.mac)
                # Seen on two APs in one poll (roaming): keep the stronger signal.
                if current is None or (client.rssi or 0) > (current.rssi or 0):
                    best[client.mac] = sighting
            uptime = result.info.uptime_seconds if result.info else None
            self._ap_states[ap.subentry_id] = AccessPointState(
                available=True,
                clients_by_band=per_band,
                info=result.info,
                # Rounded so the boot time doesn't drift by a second on every poll.
                last_boot=(now - timedelta(seconds=uptime)).replace(second=0, microsecond=0)
                if uptime is not None
                else None,
            )

        if failed == len(self.access_points):
            raise UpdateFailed("None of the access points could be read")

        self._sightings.update(best)
        return PresenceData(dict(self._sightings), dict(self._ap_states))

    def current_sighting(self, mac: str | None) -> Sighting | None:
        """The MAC's sighting if it is within the grace period, else None."""
        if not mac or self.data is None:
            return None
        sighting = self.data.sightings.get(mac)
        if sighting is None or dt_util.utcnow() - sighting.last_seen >= self.consider_home:
            return None
        return sighting
