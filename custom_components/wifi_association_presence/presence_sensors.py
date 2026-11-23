"""Presence sensors: a tracked device may name a binary sensor that is on while the
device is near, e.g. the Present sensor of a Tesla BLE ESP32 for a car. While it is
on, the device is home in that sensor's area, even off the Wi-Fi: a sleeping car
leaves the Wi-Fi but keeps advertising over Bluetooth for the phone key.

Order of evidence for where a device is: a parking spot, then its presence sensor,
then the access points.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_MAC, CONF_PRESENCE_SENSOR, SUBENTRY_TRACKED_DEVICE


@dataclass(frozen=True, slots=True)
class LocalPlace:
    """Where a device is by something closer than an access point (a parking spot
    or its presence sensor): it stands in for the access point and its area."""

    name: str | None
    area: ar.AreaEntry | None
    since: datetime | None


class PresenceSensors:
    """The presence sensors of one config entry's tracked devices."""

    def __init__(self, hass: HomeAssistant, entry, on_change: Callable[[], None]) -> None:
        self._hass = hass
        self._on_change = on_change
        # Tracked device subentry id -> its presence sensor; MAC -> subentry id.
        self.entities: dict[str, str] = {}
        self.by_mac: dict[str, str] = {}
        for sub in entry.get_subentries_of_type(SUBENTRY_TRACKED_DEVICE):
            if entity_id := sub.data.get(CONF_PRESENCE_SENSOR):
                self.entities[sub.subentry_id] = entity_id
                self.by_mac[sub.data[CONF_MAC]] = sub.subentry_id

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

    def _state(self, subentry_id: str | None) -> State | None:
        entity_id = self.entities.get(subentry_id or "")
        return self._hass.states.get(entity_id) if entity_id else None

    def is_on(self, subentry_id: str | None) -> bool | None:
        """On, off, or None (no sensor, or it is unavailable: the Wi-Fi decides)."""
        state = self._state(subentry_id)
        if state is None or state.state not in (STATE_ON, STATE_OFF):
            return None
        return state.state == STATE_ON

    def off_since(self, subentry_id: str) -> datetime | None:
        """When the sensor turned off, while it is off."""
        state = self._state(subentry_id)
        return state.last_changed if state is not None and state.state == STATE_OFF else None

    def place(self, subentry_id: str) -> LocalPlace | None:
        """While the sensor is on: its device's name, its area, and when it turned on."""
        state = self._state(subentry_id)
        if state is None or state.state != STATE_ON:
            return None
        entity = er.async_get(self._hass).async_get(state.entity_id)
        device = (
            dr.async_get(self._hass).async_get(entity.device_id)
            if entity and entity.device_id
            else None
        )
        name = (device.name_by_user or device.name) if device else state.name
        area_id = (entity.area_id if entity else None) or (device.area_id if device else None)
        area = ar.async_get(self._hass).async_get_area(area_id) if area_id else None
        return LocalPlace(name, area, state.last_changed)

    def device_ids(self) -> set[str]:
        """The sensors' devices (their area is the tracked devices' area)."""
        registry = er.async_get(self._hass)
        ids: set[str] = set()
        for entity_id in self.entities.values():
            entity = registry.async_get(entity_id)
            if entity and entity.device_id:
                ids.add(entity.device_id)
        return ids
