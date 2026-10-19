"""Poll every configured access point and remember where each MAC was last seen.

The presence rules and the state across polls are the library's (PresenceTracker in
wifi_ap_associations.tracker, shared with programs outside Home Assistant); this
coordinator schedules the polls, stores the data, logs, and links access points to
Home Assistant's devices and areas.
"""

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
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from wifi_ap_associations import DRIVERS, AccessPointDriver, AccessPointError, AccessPointInfo
from wifi_ap_associations import snmp_trap
from wifi_ap_associations.coverage import AccessPointCoverage, CoverageTracker
from wifi_ap_associations.presence import AccessPointRead, AccessPointState, Sighting
from wifi_ap_associations.tracker import (
    AllAccessPointsFailed,
    PresenceSnapshot,
    PresenceTracker,
    poll_all,
    watch_forever,
)
from .const import (
    CONF_CONSIDER_HOME,
    CONF_DRIVER,
    CONF_FAST_DEPARTURE,
    CONF_INSTANT_ARRIVALS,
    CONF_SNMP_TRAP_PORT,
    CONF_NAME,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_FAST_DEPARTURE,
    DEFAULT_INSTANT_ARRIVALS,
    DEFAULT_SNMP_TRAP_PORT,
    DOMAIN,
    LOGGER,
    SCAN_INTERVAL,
    SUBENTRY_ACCESS_POINT,
    TRIGGERED_POLL_COOLDOWN,
    WATCH_RETRY_MAX,
    WATCH_RETRY_MIN,
    WATCH_STABLE,
)
from .sources import build_driver

__all__ = ["AccessPointState", "Sighting"]

type WifiAssociationConfigEntry = ConfigEntry[AssociationCoordinator]

# Coordinator data: sightings by MAC and access point state by subentry id (the
# subentry id is the tracker's access point id).
PresenceData = PresenceSnapshot

STORAGE_VERSION = 1
# Sightings and visits are written at most this often, plus on unload and when Home
# Assistant stops (it does not unload entries then): a few minutes of staleness after a
# crash is fine, a write per poll is not (SD cards).
STORAGE_SAVE_INTERVAL = timedelta(minutes=10)


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
        self._failing: set[str] = set()
        self._next_save: datetime | None = None
        # Set by the save at stop/unload; no periodic write may replace it afterwards.
        self._final_save_done = False
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.sightings"
        )
        # Sightings, visits, AP states and coverage across polls (the library's rules).
        fast = entry.options.get(CONF_FAST_DEPARTURE, DEFAULT_FAST_DEPARTURE)
        self.tracker = PresenceTracker(
            timedelta(seconds=entry.options.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)),
            poll_interval=SCAN_INTERVAL,
            fast_departure=timedelta(seconds=fast) if fast else None,
        )
        # D-Link DAPs report joins as SNMP traps to this port (one listener for all).
        snmp_trap.LISTENER.configure(
            port=int(entry.options.get(CONF_SNMP_TRAP_PORT, DEFAULT_SNMP_TRAP_PORT))
        )
        # Fast departure: the polls due at the deadlines after leaves, one per leave (a
        # later leave never moves an earlier deadline).
        self._departure_polls: set[Any] = set()
        entry.async_on_unload(self._cancel_departure_polls)
        # Instant arrivals: at most one task asking for a refresh at a time; changes
        # reported meanwhile only mark that it should ask once more.
        self._trigger_task: asyncio.Task[None] | None = None
        self._trigger_again = False

    @property
    def consider_home(self) -> timedelta:
        """The grace period in use (never under one poll)."""
        return self.tracker.consider_home

    @property
    def coverage(self) -> CoverageTracker:
        """Coverage counts of the day per device (roams, short drops, late roams)."""
        return self.tracker.coverage

    @property
    def access_point_coverage(self) -> dict[str, AccessPointCoverage]:
        """Client signal per access point in the latest poll."""
        return self.tracker.access_point_coverage

    @property
    def has_access_points(self) -> bool:
        """Whether any access point is configured (else presence is unknown)."""
        return bool(self.access_points)

    async def async_restore_sightings(self) -> None:
        """Load the sightings saved before the last reload or restart."""
        stored = await self._store.async_load()
        now = dt_util.utcnow()
        # Visits, and today's coverage counts, survive a reload or restart.
        self.tracker.restore(stored, now, dt_util.as_local(now).date())

    async def async_save_sightings(self) -> None:
        """Save now (on unload and on stop, so the next setup starts from current data)."""
        self._final_save_done = True
        await self._store.async_save(self._storage_data(clean=True))

    def _storage_data(self, clean: bool = False) -> dict[str, Any]:
        """What is saved: the sightings and today's coverage counts."""
        return self.tracker.to_storage(dt_util.utcnow(), clean=clean)

    async def _async_update_data(self) -> PresenceData:
        """Read every AP in parallel; keep previous sightings for APs that fail."""
        if not self.access_points:
            now = dt_util.utcnow()
            return self.tracker.update([], now, dt_util.as_local(now).date())

        started = time.monotonic()
        # The reads happen between this and the end of the round (fast departure).
        round_started = dt_util.utcnow()
        results = await poll_all([ap.driver for ap in self.access_points])
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
        try:
            # A short total outage (e.g. a controller restart) is ridden out on the
            # previous sightings, like a single failing AP; past the grace period (and
            # at the first refresh, so setup is retried) it fails.
            data = self.tracker.update(
                reads, now, dt_util.as_local(now).date(), started=round_started
            )
        except AllAccessPointsFailed as err:
            raise UpdateFailed(str(err)) from err
        if self.tracker.last_success != now:
            LOGGER.debug("No access point could be read; within the grace period")
            return data
        if not self._final_save_done and (self._next_save is None or now >= self._next_save):
            self._store.async_delay_save(self._storage_data)
            self._next_save = now + STORAGE_SAVE_INTERVAL
        return data

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
        await watch_forever(
            ap.driver,
            self._on_change,
            on_leave=(lambda: self._on_leave(ap)) if self.tracker.fast_departure else None,
            on_paused=lambda reason: LOGGER.info(
                "Instant arrivals for %s paused (%s); polling every minute goes on",
                ap.title,
                reason,
            ),
            on_resumed=lambda: LOGGER.info("Instant arrivals for %s work again", ap.title),
            on_failed_again=lambda reason: LOGGER.debug(
                "Watching %s again failed: %s", ap.title, reason
            ),
            retry_min=WATCH_RETRY_MIN,
            retry_max=WATCH_RETRY_MAX,
            stable=WATCH_STABLE,
        )

    @callback
    def _on_leave(self, ap: ConfiguredAccessPoint) -> None:
        """An access point reported a client leaving (fast departure is on): note it,
        and poll at the deadline so a device still missing then turns away on time."""
        fast = self.tracker.fast_departure
        if fast is None:
            return
        LOGGER.debug("Client left %s: devices missing from it are away in %s", ap.title, fast)
        self.tracker.note_leave(ap.subentry_id, dt_util.utcnow())

        @callback
        def _due(_now: datetime) -> None:
            self._departure_polls.discard(cancel)
            self._on_change()

        cancel = async_call_later(self.hass, fast.total_seconds() + 1, _due)
        self._departure_polls.add(cancel)

    @callback
    def _cancel_departure_polls(self) -> None:
        for cancel in self._departure_polls:
            cancel()
        self._departure_polls.clear()

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
        if self.data is None:
            return None
        return self.tracker.current(mac, dt_util.utcnow())
