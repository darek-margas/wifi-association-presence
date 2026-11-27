"""Parking spots in Home Assistant: feed parking.py the spots' entity states, and
place the tracked devices that use parking spots (cars).

A spot that identifies a car outranks every other source: the car is home and in
the spot's area, whatever Wi-Fi (or later Bluetooth) says. A sleeping Tesla leaves
the Wi-Fi within the hour, and its Bluetooth advert at an ESP32 by the garage door
is as strong whether it is parked inside or on the driveway.

A spot whose occupancy sensor stops reporting (its ESP32 restarts, or Home Assistant
starts) keeps its car for the grace period, as a Wi-Fi device stays home while its
access point can't be read. The car in each spot is saved with the sightings, so a
restart doesn't make a parked car leave and arrive again.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import STATE_ON, STATE_OFF, UnitOfLength
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DISTANCE,
    CONF_FLOOR_CM,
    CONF_MAC,
    CONF_MAX_HEIGHT_CM,
    CONF_MIN_HEIGHT_CM,
    CONF_OCCUPIED,
    CONF_PARKING,
    CONF_RSSI,
    CONF_RSSI_MARGIN,
    CONF_RSSI_MIN,
    DOMAIN,
    SUBENTRY_PARKING_SPOT,
    SUBENTRY_TRACKED_DEVICE,
)
from .parking import CarPlace, Location, SpotConfig, SpotKind, SpotState, SpotTracker, locate_cars
from .presence_sensors import RESTORE_MAX_AGE, parse_time

# A distance sensor in another unit is converted to centimetres.
_TO_CM = {UnitOfLength.CENTIMETERS: 1.0, UnitOfLength.MILLIMETERS: 0.1, UnitOfLength.METERS: 100.0}


@dataclass(frozen=True, slots=True)
class ParkingCar:
    """A tracked device that uses parking spots."""

    subentry_id: str
    title: str
    mac: str


@dataclass(slots=True)
class ConfiguredSpot:
    """A parking spot subentry and the state across updates (the latched car)."""

    subentry_id: str
    title: str
    occupied: str
    distance: str | None
    # Car (tracked device subentry id) -> the entity with its RSSI at this spot.
    rssi: dict[str, str]
    tracker: SpotTracker = field(default_factory=SpotTracker)

    def entity_ids(self) -> set[str]:
        ids = {self.occupied, *self.rssi.values()}
        if self.distance:
            ids.add(self.distance)
        return ids


def spot_config(data: dict) -> SpotConfig:
    """The spot's thresholds, defaults for any not saved."""
    d = SpotConfig()
    return SpotConfig(
        floor_cm=float(data.get(CONF_FLOOR_CM, d.floor_cm)),
        min_height_cm=float(data.get(CONF_MIN_HEIGHT_CM, d.min_height_cm)),
        max_height_cm=float(data.get(CONF_MAX_HEIGHT_CM, d.max_height_cm)),
        rssi_min=float(data.get(CONF_RSSI_MIN, d.rssi_min)),
        rssi_margin=float(data.get(CONF_RSSI_MARGIN, d.rssi_margin)),
    )


class Parking:
    """The parking spots of one config entry and where they place the cars."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry,
        grace: timedelta,
        present: Callable[[str], bool | None],
        arrived: Callable[[str], datetime | None],
        on_change: Callable[[], None],
    ) -> None:
        """grace: how long a spot keeps its car while its sensor doesn't report.
        present(mac): whether the other sources (Wi-Fi) have the car home, None if
        they can't tell. arrived(mac): when they saw its current visit start, if it is
        home by them. on_change: called when a spot or a car's place changed."""
        self._hass = hass
        self._entry_id = entry.entry_id
        self._grace = grace
        self._present = present
        self._arrived = arrived
        self._on_change = on_change
        self.cars: dict[str, ParkingCar] = {
            sub.subentry_id: ParkingCar(sub.subentry_id, sub.title, sub.data[CONF_MAC])
            for sub in entry.get_subentries_of_type(SUBENTRY_TRACKED_DEVICE)
            if sub.data.get(CONF_PARKING)
        }
        self.spots: dict[str, ConfiguredSpot] = {}
        for sub in entry.get_subentries_of_type(SUBENTRY_PARKING_SPOT):
            spot = ConfiguredSpot(
                sub.subentry_id,
                sub.title,
                sub.data[CONF_OCCUPIED],
                sub.data.get(CONF_DISTANCE) or None,
                {car: entity for car, entity in sub.data.get(CONF_RSSI, {}).items() if entity},
            )
            spot.tracker.config = spot_config(dict(sub.data))
            self.spots[sub.subentry_id] = spot
        self.spot_states: dict[str, SpotState] = {}
        self.places: dict[str, CarPlace] = {}
        # When each car was identified in its spot, and when it last left one.
        self._since: dict[str, datetime] = {}
        self._left: dict[str, datetime] = {}
        # Spots not reporting while they held a car: spot -> (the car, since when).
        self._silent: dict[str, tuple[str, datetime]] = {}

    @property
    def active(self) -> bool:
        return bool(self.cars and self.spots)

    @callback
    def async_start(self) -> Callable[[], None]:
        """Follow the spots' sensors; returns the unsubscribe."""
        ids = {i for spot in self.spots.values() for i in spot.entity_ids()}
        if not self.active or not ids:
            return lambda: None
        unsubscribe = async_track_state_change_event(self._hass, sorted(ids), self._state_changed)
        # What changed between the first poll and now (the platforms were being set up).
        if self.evaluate():
            self._on_change()
        return unsubscribe

    @callback
    def _state_changed(self, _event: Event[EventStateChangedData]) -> None:
        if self.evaluate():
            self._on_change()

    def evaluate(self) -> bool:
        """Read the spots and place the cars; whether anything changed."""
        if not self.active:
            return False
        now = dt_util.utcnow()
        states: dict[str, SpotState] = {}
        present = {car_id: self._present(car.mac) for car_id, car in self.cars.items()}
        monotonic = time.monotonic()
        for spot in self.spots.values():
            rssi = {car: self._number(entity) for car, entity in spot.rssi.items() if car in self.cars}
            if not rssi and len(self.cars) == 1:
                # One car uses parking spots and has no RSSI here: anything car-shaped
                # in the spot is that car (a guest car too; an RSSI tells them apart).
                rssi = {next(iter(self.cars)): 0.0}
            state = spot.tracker.update(
                self._occupied(spot.occupied), self._distance_cm(spot.distance), rssi,
                present, monotonic,
            )
            states[spot.subentry_id] = self._ride_out(spot.subentry_id, state, now)
        places = locate_cars(present, states)
        for car_id, place in places.items():
            before = self.places.get(car_id)
            in_spot = place.location is Location.IN_SPOT
            was_in = before is not None and before.location is Location.IN_SPOT
            if in_spot and (not was_in or before.spot != place.spot):
                # Arrived in the spot: the visit started then, or when the Wi-Fi saw
                # it arrive if it was already home (so arrived_at doesn't move).
                self._since[car_id] = self._arrived(self.cars[car_id].mac) or now
            elif was_in and not in_spot:
                self._left[car_id] = now
                self._since.pop(car_id, None)
        changed = places != self.places or _kinds(states) != _kinds(self.spot_states)
        self.spot_states = states
        self.places = places
        return changed

    def _ride_out(self, spot_id: str, state: SpotState, now: datetime) -> SpotState:
        """A spot that held a car and stops reporting keeps it for the grace period."""
        if state.kind is not SpotKind.UNKNOWN:
            self._silent.pop(spot_id, None)
            return state
        held = self._silent.get(spot_id)
        if held is None:
            before = self.spot_states.get(spot_id)
            if before is None or before.kind is not SpotKind.CAR or before.car_id is None:
                return state
            held = self._silent[spot_id] = (before.car_id, now)
        if held[0] in self.cars and now - held[1] <= self._grace:
            return SpotState(SpotKind.CAR, held[0])
        # Past the grace period: unknown. The entry stays (no car is held from it
        # again) until the sensor reports.
        return state

    def to_storage(self) -> dict[str, Any]:
        """The car in each spot and the cars' times, to take up after a restart."""
        return {
            "saved": dt_util.utcnow().isoformat(),
            "spots": {
                spot_id: state.car_id
                for spot_id, state in self.spot_states.items()
                if state.kind is SpotKind.CAR and state.car_id
            },
            "since": {car: at.isoformat() for car, at in self._since.items()},
            "left": {car: at.isoformat() for car, at in self._left.items()},
        }

    def restore(self, stored: Mapping[str, Any] | None) -> None:
        """Take up what to_storage() saved. A spot that held a car holds it again, as
        not reporting from now: its sensor has the grace period to report (its ESP32
        may not be connected yet). If it then reports occupied, the car stays; if
        empty, it left. Saved too long ago, the spots start afresh."""
        stored = stored or {}
        now = dt_util.utcnow()
        for car, at in (stored.get("left") or {}).items():
            if car in self.cars and (when := parse_time(at)):
                self._left[car] = when
        saved_at = parse_time(stored.get("saved"))
        if saved_at is None or now - saved_at > RESTORE_MAX_AGE:
            return
        since = stored.get("since") or {}
        for spot_id, car in (stored.get("spots") or {}).items():
            spot = self.spots.get(spot_id)
            if spot is None or car not in self.cars:
                continue
            spot.tracker.restore(car)  # latched, as it was
            self.spot_states[spot_id] = SpotState(SpotKind.CAR, car)
            self.places[car] = CarPlace(Location.IN_SPOT, spot_id)
            self._silent[spot_id] = (car, now)
            self._since[car] = parse_time(since.get(car)) or now

    def spot_of(self, car_id: str) -> ConfiguredSpot | None:
        """The spot the car is identified in, if any."""
        place = self.places.get(car_id)
        if place is None or place.location is not Location.IN_SPOT or place.spot is None:
            return None
        return self.spots.get(place.spot)

    def in_spot_since(self, car_id: str) -> datetime | None:
        return self._since.get(car_id) if self.spot_of(car_id) else None

    def left_spot_at(self, car_id: str) -> datetime | None:
        return self._left.get(car_id)

    def occupant(self, spot_id: str) -> SpotState | None:
        return self.spot_states.get(spot_id)

    def spot_device(self, spot_id: str) -> dr.DeviceEntry | None:
        return dr.async_get(self._hass).async_get_device_by_identifier(
            (DOMAIN, spot_id), self._entry_id
        )

    def spot_area(self, spot_id: str) -> ar.AreaEntry | None:
        """The area the user gave the spot's device."""
        device = self.spot_device(spot_id)
        if device is None or device.area_id is None:
            return None
        return ar.async_get(self._hass).async_get_area(device.area_id)

    def spot_device_ids(self) -> set[str]:
        return {d.id for spot_id in self.spots if (d := self.spot_device(spot_id))}

    def _number(self, entity_id: str | None) -> float | None:
        state = self._hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None  # unknown, unavailable

    def _occupied(self, entity_id: str) -> bool | None:
        state = self._hass.states.get(entity_id)
        if state is None or state.state not in (STATE_ON, STATE_OFF):
            return None
        return state.state == STATE_ON

    def _distance_cm(self, entity_id: str | None) -> float | None:
        value = self._number(entity_id)
        if value is None:
            return None
        unit = self._hass.states.get(entity_id).attributes.get("unit_of_measurement")
        return value * _TO_CM.get(unit, 1.0)


def _kinds(states: dict[str, SpotState]) -> dict[str, tuple[SpotKind, str | None]]:
    """What a spot shows, without the height (it changes with every reading)."""
    return {spot: (s.kind, s.car_id) for spot, s in states.items()}


def suggested_spot_area(hass: HomeAssistant, occupied_entity: str) -> str | None:
    """The area of the occupancy sensor (its own, else its device's), for a new spot."""
    entity = er.async_get(hass).async_get(occupied_entity)
    if entity is None:
        return None
    area_id = entity.area_id
    if area_id is None and entity.device_id:
        device = dr.async_get(hass).async_get(entity.device_id)
        area_id = device.area_id if device else None
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return area.name if area else None
