"""Presence sensors: a tracked device may name a binary sensor that is on while the
device is near, e.g. the Present sensor of a Tesla BLE ESP32 for a car. While it is
on, the device is home in that sensor's area, even off the Wi-Fi: a sleeping car
leaves the Wi-Fi but keeps advertising over Bluetooth for the phone key.

Order of evidence for where a device is: a parking spot, then its presence sensor,
then the access points.

A sensor that stops reporting (unavailable while its ESP32 restarts, or while Home
Assistant starts) is not evidence of leaving: the device stays home for the grace
period, as a Wi-Fi device does while its access point can't be read. The times of a
visit are kept here and saved with the sightings, because Home Assistant's own
last_changed starts again at every restart.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import CONF_MAC, CONF_PRESENCE_SENSOR, SUBENTRY_TRACKED_DEVICE

# What was saved longer ago than this is not taken up again at a restart: too much
# may have happened meanwhile (the same limit as for a parking spot's car).
RESTORE_MAX_AGE = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class LocalPlace:
    """Where a device is by something closer than an access point (a parking spot
    or its presence sensor): it stands in for the access point and its area."""

    name: str | None
    area: ar.AreaEntry | None
    since: datetime | None


@dataclass(slots=True)
class _Record:
    """What one sensor last said for certain, and since when."""

    # True: on (or not reporting, within the grace period after on). False: off.
    # None: nothing known (never reported, or not reporting for longer than the grace).
    on: bool | None = None
    # When this run of "on" started.
    since: datetime | None = None
    # While on but not reporting: since when.
    silent_since: datetime | None = None
    # When it was last known on, once it no longer is.
    last_on: datetime | None = None


def parse_time(value: Any) -> datetime | None:
    """A saved ISO time, if it is one with a timezone."""
    try:
        when = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo is not None else None


class PresenceSensors:
    """The presence sensors of one config entry's tracked devices."""

    def __init__(
        self, hass: HomeAssistant, entry, grace: timedelta, on_change: Callable[[], None]
    ) -> None:
        self._hass = hass
        self._grace = grace
        self._on_change = on_change
        # Tracked device subentry id -> its presence sensor; MAC -> subentry id.
        self.entities: dict[str, str] = {}
        self.by_mac: dict[str, str] = {}
        for sub in entry.get_subentries_of_type(SUBENTRY_TRACKED_DEVICE):
            if entity_id := sub.data.get(CONF_PRESENCE_SENSOR):
                self.entities[sub.subentry_id] = entity_id
                self.by_mac[sub.data[CONF_MAC]] = sub.subentry_id
        self._records: dict[str, _Record] = {}

    @callback
    def async_start(self) -> Callable[[], None]:
        """Follow the sensors; returns the unsubscribe."""
        if not self.entities:
            return lambda: None
        return async_track_state_change_event(
            self._hass, sorted(set(self.entities.values())), self._state_changed
        )

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if (old and old.state) != (new and new.state):  # attribute-only changes don't matter
            self._on_change()

    def _reported(self, subentry_id: str) -> bool | None:
        """What the sensor says right now: on, off, or nothing (None)."""
        state = self._hass.states.get(self.entities[subentry_id])
        if state is None or state.state not in (STATE_ON, STATE_OFF):
            return None
        return state.state == STATE_ON

    def _record(self, subentry_id: str | None) -> _Record | None:
        """The sensor's record, brought up to date with what it reports now."""
        if not subentry_id or subentry_id not in self.entities:
            return None
        now = dt_util.utcnow()
        record = self._records.setdefault(subentry_id, _Record())
        reported = self._reported(subentry_id)
        if reported is True:
            if record.on is not True:
                record.since = now
            record.on, record.silent_since = True, None
        elif reported is False:
            if record.on is True:
                record.last_on = record.silent_since or now
            record.on, record.silent_since = False, None
        elif record.on is True:
            # Not reporting: still on for the grace period, counted from when it stopped.
            record.silent_since = record.silent_since or now
            if now - record.silent_since > self._grace:
                record.on, record.last_on, record.silent_since = None, record.silent_since, None
        else:
            record.on = None
        return record

    def is_on(self, subentry_id: str | None) -> bool | None:
        """On (also while it stopped reporting, within the grace period), off, or None
        (no sensor, or nothing known: the Wi-Fi decides)."""
        record = self._record(subentry_id)
        return record.on if record else None

    def off_since(self, subentry_id: str) -> datetime | None:
        """When the sensor was last known on, while it no longer is."""
        record = self._record(subentry_id)
        return record.last_on if record and record.on is not True else None

    def place(self, subentry_id: str) -> LocalPlace | None:
        """While the sensor is on: the device that hears it, its area, and since when."""
        record = self._record(subentry_id)
        if record is None or record.on is not True:
            return None
        entity_id = self.entities[subentry_id]
        entity = er.async_get(self._hass).async_get(entity_id)
        devices = dr.async_get(self._hass)
        device = devices.async_get(entity.device_id) if entity and entity.device_id else None
        # A sub-device (an esphome-tesla-ble-multi car on its ESP32) stands for the
        # device itself: the one it is attached to is what hears the car, and has the
        # area. Its name and area win; the sub-device's are the fallback.
        parent = devices.async_get(device.via_device_id) if device and device.via_device_id else None
        hearing = parent or device
        if hearing is not None:
            name = hearing.name_by_user or hearing.name
        else:
            state = self._hass.states.get(entity_id)
            name = state.name if state else entity_id
        area_id = (
            (entity.area_id if entity else None)
            or (device.area_id if device else None)
            or (parent.area_id if parent else None)
        )
        area = ar.async_get(self._hass).async_get_area(area_id) if area_id else None
        return LocalPlace(name, area, record.since)

    def device_ids(self) -> set[str]:
        """The sensors' devices (their area is the tracked devices' area)."""
        registry = er.async_get(self._hass)
        devices = dr.async_get(self._hass)
        ids: set[str] = set()
        for entity_id in self.entities.values():
            entity = registry.async_get(entity_id)
            if entity and entity.device_id:
                ids.add(entity.device_id)
                device = devices.async_get(entity.device_id)
                if device and device.via_device_id:
                    ids.add(device.via_device_id)
        return ids

    def to_storage(self) -> dict[str, Any]:
        """The visits to take up again after a restart."""
        saved: dict[str, Any] = {}
        for subentry_id in self.entities:
            record = self._record(subentry_id)
            if record is None or (record.on is not True and record.last_on is None):
                continue
            saved[subentry_id] = {
                "on": record.on is True,
                "since": record.since.isoformat() if record.since and record.on else None,
                "last_on": record.last_on.isoformat() if record.last_on else None,
            }
        return {"saved": dt_util.utcnow().isoformat(), "sensors": saved}

    def restore(self, stored: Mapping[str, Any] | None) -> None:
        """Take up what to_storage() saved. A sensor that was on counts as on and not
        reporting from now: it has the grace period to report again (its ESP32 may
        not be connected yet), and its visit keeps its start."""
        now = dt_util.utcnow()
        saved_at = parse_time((stored or {}).get("saved"))
        recent = saved_at is not None and now - saved_at <= RESTORE_MAX_AGE
        for subentry_id, item in ((stored or {}).get("sensors") or {}).items():
            if subentry_id not in self.entities or not isinstance(item, Mapping):
                continue
            record = _Record(last_on=parse_time(item.get("last_on")))
            if item.get("on") and recent:
                record.on = True
                record.since = parse_time(item.get("since")) or now
                record.silent_since = now
            elif item.get("on"):
                record.last_on = saved_at
            self._records[subentry_id] = record
