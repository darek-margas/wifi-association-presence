"""Poll every configured access point and remember where each MAC was last seen."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .ap_drivers import DRIVERS, AccessPointDriver, AccessPointError
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


class AssociationCoordinator(DataUpdateCoordinator[dict[str, Sighting]]):
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
        self._access_points: list[tuple[str, AccessPointDriver]] = []
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
            self._access_points.append((subentry.title, driver_cls(dict(subentry.data))))
        self._sightings: dict[str, Sighting] = {}
        self._failing: set[str] = set()
        self.consider_home = timedelta(
            seconds=entry.options.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)
        )

    @property
    def has_access_points(self) -> bool:
        """Whether any access point is configured (else presence is unknown)."""
        return bool(self._access_points)

    async def _async_update_data(self) -> dict[str, Sighting]:
        """Read every AP in parallel; keep previous sightings for APs that fail."""
        if not self._access_points:
            return {}

        results = await asyncio.gather(
            *(driver.async_get_associated_clients() for _, driver in self._access_points),
            return_exceptions=True,
        )
        now = dt_util.utcnow()
        best: dict[str, Sighting] = {}
        failed = 0
        for (title, _), result in zip(self._access_points, results, strict=True):
            if isinstance(result, AccessPointError):
                failed += 1
                if title not in self._failing:
                    LOGGER.warning("Cannot read access point %s: %s", title, result)
                    self._failing.add(title)
                continue
            if isinstance(result, BaseException):
                raise result
            if title in self._failing:
                LOGGER.info("Access point %s is readable again", title)
                self._failing.discard(title)
            for client in result:
                sighting = Sighting(title, client.ssid, client.band, client.rssi, now)
                current = best.get(client.mac)
                # Seen on two APs in one poll (roaming): keep the stronger signal.
                if current is None or (client.rssi or 0) > (current.rssi or 0):
                    best[client.mac] = sighting

        if failed == len(self._access_points):
            raise UpdateFailed("None of the access points could be read")

        self._sightings.update(best)
        return dict(self._sightings)
