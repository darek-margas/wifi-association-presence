"""Poll every configured access point and remember where each MAC was last seen."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from wifi_ap_associations import DRIVERS, AccessPointDriver, AccessPointError, AccessPointInfo
from .const import (
    CONF_CONSIDER_HOME,
    CONF_DRIVER,
    CONF_INSTANT_ARRIVALS,
    CONF_NAME,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_INSTANT_ARRIVALS,
    DOMAIN,
    LOGGER,
    SCAN_INTERVAL,
    SUBENTRY_ACCESS_POINT,
    TRIGGERED_POLL_COOLDOWN,
    WATCH_RETRY_MAX,
    WATCH_RETRY_MIN,
    WATCH_STABLE,
)
from .coverage_stats import AccessPointCoverage, CoverageTracker, access_point_coverage
from .presence import (
    AccessPointRead,
    AccessPointState,
    Sighting,
    carry_visits,
    is_present,
    merge_reads,
    prune,
    restart_visit_gap,
    ride_out_total_failure,
    sightings_from_storage,
    sightings_to_storage,
    stored_at,
    stored_cleanly,
)
from .sources import build_driver

__all__ = ["AccessPointState", "Sighting"]

type WifiAssociationConfigEntry = ConfigEntry[AssociationCoordinator]

# Sightings are kept (and stored across reloads and restarts) this long, so devices that
# sleep with Wi-Fi off (cars, tablets) can still be picked when adding a tracked device.
SIGHTING_RETENTION = timedelta(days=7)
STORAGE_VERSION = 1
# Sightings and visits are written at most this often, plus on unload and when Home
# Assistant stops (it does not unload entries then): a few minutes of staleness after a
# crash is fine, a write per poll is not (SD cards).
STORAGE_SAVE_INTERVAL = timedelta(minutes=10)
# A device read in the latest poll must count as present, so a shorter grace period
# acts as this one: away after one missed poll.
MIN_CONSIDER_HOME = timedelta(seconds=30)
# Sightings this close together belong to the same visit even with a short grace
# period (consecutive polls are SCAN_INTERVAL apart, give or take a slow AP).
MIN_VISIT_GAP = SCAN_INTERVAL + timedelta(seconds=30)


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
    # Whether the user gave it a name; otherwise the name it reports is preferred.
    named: bool

    def display_name(self, info: AccessPointInfo | None) -> str:
        """The user's name, else the name the AP reports about itself, else the title."""
        if self.named or info is None or not info.name:
            return self.title
        return info.name


def user_named(subentry: ConfigSubentry) -> bool:
    """Whether the user named this access point.

    Stored explicitly since 0.3.0; older entries had no name in their data, and their
    title was the host unless the user typed a name.
    """
    if CONF_NAME in subentry.data:
        return bool(subentry.data[CONF_NAME])
    return subentry.title != subentry.data.get("host")


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
            # Polls asked for by instant-arrival events: at once, then at most one per
            # cooldown, so a burst of changes is one poll.
            request_refresh_debouncer=Debouncer(
                hass, LOGGER, cooldown=TRIGGERED_POLL_COOLDOWN, immediate=True
            ),
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
            try:
                driver = build_driver(hass, driver_cls, dict(subentry.data))
            except (KeyError, ValueError) as err:
                # A bad or missing setting only loses this AP, never the whole entry.
                LOGGER.error(
                    "Access point %s has an invalid setting (%s); skipping it",
                    subentry.title,
                    err,
                )
                continue
            self.access_points.append(
                ConfiguredAccessPoint(
                    subentry.subentry_id, subentry.title, driver, named=user_named(subentry)
                )
            )
        self._sightings: dict[str, Sighting] = {}
        self._ap_states: dict[str, AccessPointState] = {}
        self._failing: set[str] = set()
        # Last poll in which at least one access point was read; a poll in which none
        # could be is ridden out only within the grace period after it.
        self._last_success: datetime | None = None
        self._next_save: datetime | None = None
        # When the restored sightings file was written, and whether at stop/unload
        # (clean) or periodically; None once the first poll used it.
        self._restored_at: datetime | None = None
        self._restored_clean = False
        # Set by the save at stop/unload; no periodic write may replace it afterwards.
        self._final_save_done = False
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.sightings"
        )
        self.consider_home = max(
            timedelta(seconds=entry.options.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)),
            MIN_CONSIDER_HOME,
        )
        self._visit_gap = max(self.consider_home, MIN_VISIT_GAP)
        # Coverage sensors: per-device counts of the day, per-AP client signal.
        self.coverage = CoverageTracker()
        self.access_point_coverage: dict[str, AccessPointCoverage] = {}
        # Instant arrivals: at most one task asking for a refresh at a time; changes
        # reported meanwhile only mark that it should ask once more.
        self._trigger_task: asyncio.Task[None] | None = None
        self._trigger_again = False

    @property
    def has_access_points(self) -> bool:
        """Whether any access point is configured (else presence is unknown)."""
        return bool(self.access_points)

    async def async_restore_sightings(self) -> None:
        """Load the sightings saved before the last reload or restart."""
        stored = await self._store.async_load()
        self._sightings = sightings_from_storage(stored, dt_util.utcnow(), SIGHTING_RETENTION)
        self._restored_at = stored_at(stored)
        self._restored_clean = stored_cleanly(stored)
        # Today's coverage counts (roams, drops, late roams) survive a same-day restart.
        self.coverage.restore(
            (stored or {}).get("coverage"), dt_util.as_local(dt_util.utcnow()).date()
        )

    async def async_save_sightings(self) -> None:
        """Save now (on unload and on stop, so the next setup starts from current data)."""
        self._final_save_done = True
        await self._store.async_save(self._storage_data(clean=True))

    def _storage_data(self, clean: bool = False) -> dict[str, Any]:
        """What is saved: the sightings and today's coverage counts."""
        data = sightings_to_storage(self._sightings, dt_util.utcnow(), clean=clean)
        data["coverage"] = self.coverage.to_storage()
        return data

    async def _async_update_data(self) -> PresenceData:
        """Read every AP in parallel; keep previous sightings for APs that fail."""
        self.coverage.reset_day(dt_util.as_local(dt_util.utcnow()).date())
        if not self.access_points:
            return PresenceData(dict(self._sightings), {})

        started = time.monotonic()
        results = await asyncio.gather(
            *(
                asyncio.wait_for(ap.driver.async_poll(), ap.driver.POLL_TIMEOUT)
                for ap in self.access_points
            ),
            return_exceptions=True,
        )
        LOGGER.debug(
            "Polled %d access point(s) in %.2f s", len(self.access_points), time.monotonic() - started
        )
        reads: list[AccessPointRead] = []
        for ap, result in zip(self.access_points, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result  # cancellation and the like are not AP failures
            reads.append(
                AccessPointRead(
                    ap.subentry_id,
                    self.sighting_name(ap, None if isinstance(result, Exception) else result.info),
                    ap.driver.SIGNAL_UNIT,
                    result,
                )
            )
            self._log_read(ap, result)

        now = dt_util.utcnow()
        merged = merge_reads(reads, now)
        self._ap_states.update(merged.states)
        for subentry_id in merged.failed:
            # Keep what the AP last reported about itself (name, firmware) while down.
            previous = self._ap_states.get(subentry_id)
            self._ap_states[subentry_id] = AccessPointState(
                available=False, info=previous.info if previous else None
            )
        if len(merged.failed) == len(self.access_points):
            # Nobody was observed: a device seen on another AP afterwards is no roam.
            self.coverage.interrupt()
            # A short total outage (e.g. a controller restart) is ridden out on the
            # previous sightings, like a single failing AP; the first refresh still
            # fails, so setup is retried.
            if not ride_out_total_failure(self._last_success, now, self.consider_home):
                raise UpdateFailed("None of the access points could be read")
            LOGGER.debug("No access point could be read; within the grace period")
            return PresenceData(dict(self._sightings), dict(self._ap_states))
        self._last_success = now
        self.coverage.update(
            merged.sightings,
            merged.failed,
            now,
            dt_util.as_local(now).date(),
            self.consider_home,
        )
        self.access_point_coverage = access_point_coverage(merged.sightings)

        visit_gap = self._visit_gap
        if self._restored_at is not None:
            # First poll after a restart or reload (see restart_visit_gap).
            visit_gap = restart_visit_gap(
                visit_gap, self._restored_at, self._restored_clean, now
            )
            self._restored_at = None
        seen = carry_visits(merged.sightings, self._sightings, now, visit_gap)
        self._sightings = prune({**self._sightings, **seen}, now, SIGHTING_RETENTION)
        if not self._final_save_done and (self._next_save is None or now >= self._next_save):
            self._store.async_delay_save(self._storage_data)
            self._next_save = now + STORAGE_SAVE_INTERVAL
        return PresenceData(dict(self._sightings), dict(self._ap_states))

    @callback
    def async_start_watchers(self) -> None:
        """Instant arrivals: watch each access point (or controller) that can report
        changes, unless the option is off. One watch per watch key, so the CAPs of one
        CAPsMAN controller share one. The tasks end with the entry."""
        if not self.config_entry.options.get(CONF_INSTANT_ARRIVALS, DEFAULT_INSTANT_ARRIVALS):
            return
        watched: set[Any] = set()
        for ap in self.access_points:
            if not ap.driver.WATCHES or (key := ap.driver.watch_key()) in watched:
                continue
            watched.add(key)
            self.config_entry.async_create_background_task(
                self.hass, self._async_watch(ap), f"{DOMAIN} watch {ap.title}"
            )

    async def _async_watch(self, ap: ConfiguredAccessPoint) -> None:
        """Keep one access point's change feed open, starting it again when it ends.

        A change only asks for a poll; the poll decides who is there. A feed that fails
        is retried after a growing wait, logged once when it stops and once when it
        works again; polling every minute goes on regardless.
        """
        delay = WATCH_RETRY_MIN
        paused = False
        while True:
            started = time.monotonic()
            try:
                await ap.driver.async_watch(self._on_change)
                reason = "the feed ended"
            except asyncio.CancelledError:
                raise
            except AccessPointError as err:
                reason = str(err) or type(err).__name__
            except Exception:  # a driver bug must not end the watching for good
                LOGGER.exception("Unexpected error watching access point %s", ap.title)
                reason = "unexpected error"
            if time.monotonic() - started >= WATCH_STABLE:
                delay = WATCH_RETRY_MIN
                if paused:
                    LOGGER.info("Instant arrivals for %s work again", ap.title)
                    paused = False
            if not paused:
                LOGGER.info(
                    "Instant arrivals for %s paused (%s); polling every minute goes on",
                    ap.title,
                    reason,
                )
                paused = True
            else:
                LOGGER.debug("Watching %s again failed: %s", ap.title, reason)
            await asyncio.sleep(delay)
            delay = min(delay * 2, WATCH_RETRY_MAX)

    @callback
    def _on_change(self) -> None:
        """An access point reported a change: poll soon (debounced).

        A burst of changes makes one task, not one per change: while it runs, further
        changes only ask it to request once more when done, so a client that joins
        during a refresh is still picked up.
        """
        if self._trigger_task is not None and not self._trigger_task.done():
            LOGGER.debug("Change reported during a triggered refresh: one more to follow")
            self._trigger_again = True
            return
        LOGGER.debug("Change reported: asking for a refresh")
        self._trigger_task = self.hass.async_create_task(self._async_triggered_refresh())

    async def _async_triggered_refresh(self) -> None:
        while True:
            self._trigger_again = False
            await self.async_request_refresh()
            if not self._trigger_again:
                return

    def _log_read(self, ap: ConfiguredAccessPoint, result: object) -> None:
        """Log an AP going unreadable once, and coming back."""
        if not isinstance(result, Exception):
            if ap.title in self._failing:
                LOGGER.info("Access point %s is readable again", ap.title)
                self._failing.discard(ap.title)
            return
        if ap.title in self._failing:
            return
        self._failing.add(ap.title)
        # Any failure of one AP, expected or a driver bug, only makes that AP
        # unreadable; it never stops the others from updating.
        if isinstance(result, (AccessPointError, TimeoutError)):
            LOGGER.warning("Cannot read access point %s: %s", ap.title, result or "timeout")
        else:
            LOGGER.error("Unexpected error reading access point %s", ap.title, exc_info=result)

    def access_point_name(self, ap: ConfiguredAccessPoint) -> str:
        """Name to show for an access point (see ConfiguredAccessPoint.display_name)."""
        state = self.data.access_points.get(ap.subentry_id) if self.data else None
        return ap.display_name(state.info if state else None)

    def sighting_name(self, ap: ConfiguredAccessPoint, info: AccessPointInfo | None) -> str:
        """The access point's name on the devices it sees: the name the user gave its
        device in Home Assistant if they renamed it, else its display name (#11: a CAP
        showed its RouterOS identity, which the user had renamed in Home Assistant)."""
        device = self.access_point_device(ap)
        if device is not None and device.name_by_user:
            return device.name_by_user
        return ap.display_name(info)

    def access_point_device(self, ap: ConfiguredAccessPoint) -> dr.DeviceEntry | None:
        """The device that carries the access point's area.

        Our own for drivers with OWN_DEVICE; else the one another integration (e.g.
        UniFi Network) registered for the AP's MAC. Devices belong to one config entry
        each, so that one can't be shared, only looked up; if several integrations
        registered the MAC, the one with an area wins.
        """
        registry = dr.async_get(self.hass)
        if ap.driver.OWN_DEVICE:
            return registry.async_get_device_by_identifier(
                (DOMAIN, ap.subentry_id), self.config_entry.entry_id
            )
        mac = ap.driver.device_mac()
        if not mac:
            return None
        devices = registry.async_get_devices(
            connections={(dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))}
        )
        return next((d for d in devices if d.area_id), devices[0] if devices else None)

    def access_point_device_ids(self) -> set[str]:
        """Ids of the devices whose area changes affect the tracked devices."""
        ids: set[str] = set()
        for ap in self.access_points:
            if device := self.access_point_device(ap):
                ids.add(device.id)
        return ids

    def access_point_area(self, subentry_id: str) -> ar.AreaEntry | None:
        """The Home Assistant area the user assigned to an access point's device."""
        ap = next((ap for ap in self.access_points if ap.subentry_id == subentry_id), None)
        device = self.access_point_device(ap) if ap else None
        if device is None or device.area_id is None:
            return None
        return ar.async_get(self.hass).async_get_area(device.area_id)

    def current_sighting(self, mac: str | None) -> Sighting | None:
        """The MAC's sighting if it is within the grace period, else None."""
        if not mac or self.data is None:
            return None
        sighting = self.data.sightings.get(mac)
        return sighting if is_present(sighting, dt_util.utcnow(), self.consider_home) else None
