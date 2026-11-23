"""Sensors: access point details, where each tracked device is connected, and the
parking spots."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MAC, CONF_PARKING, DOMAIN, SUBENTRY_PARKING_SPOT, SUBENTRY_TRACKED_DEVICE
from .coordinator import (
    AccessPointState,
    AssociationCoordinator,
    ConfiguredAccessPoint,
    Sighting,
    WifiAssociationConfigEntry,
)
from wifi_ap_associations.coverage import AccessPointCoverage, DeviceCounts
from .entity import access_point_device_info, parking_spot_device_info, tracked_device_info
from .parking import Location, SpotKind
from .presence_sensors import LocalPlace

type StateValue = str | int | datetime | None


@dataclass(frozen=True, kw_only=True)
class AccessPointSensorDescription(SensorEntityDescription):
    """An access point sensor and how to read it from the AP's state."""

    value_fn: Callable[[AccessPointState], StateValue]
    # Coverage sensors read the latest poll's client signal instead (value_fn unused).
    coverage_fn: Callable[[AccessPointCoverage | None], StateValue] | None = None
    # The AccessPointInfo field the value comes from, if any: the sensor is only created
    # for drivers that report it (AccessPointDriver.REPORTS).
    info_field: str | None = None
    # The client detail it needs ("band" or "signal"), if any: only created for drivers
    # that give it (AccessPointDriver.CLIENT_DETAILS).
    client_detail: str | None = None


ACCESS_POINT_SENSORS = (
    AccessPointSensorDescription(
        key="clients_2_4ghz",
        translation_key="clients_2_4ghz",
        client_detail="band",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.clients_by_band.get("2.4GHz", 0),
    ),
    AccessPointSensorDescription(
        key="clients_5ghz",
        translation_key="clients_5ghz",
        client_detail="band",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.clients_by_band.get("5GHz", 0),
    ),
    AccessPointSensorDescription(
        key="clients_total",
        translation_key="clients_total",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: sum(s.clients_by_band.values()),
    ),
    # CPU and memory change on nearly every poll, so each is a recorder row per AP per
    # minute; opt-in, as most people want the client counts, not AP health.
    AccessPointSensorDescription(
        key="cpu",
        translation_key="cpu",
        info_field="cpu_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.info.cpu_percent if s.info else None,
    ),
    AccessPointSensorDescription(
        key="memory",
        translation_key="memory",
        info_field="memory_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.info.memory_percent if s.info else None,
    ),
    AccessPointSensorDescription(
        key="last_boot",
        translation_key="last_boot",
        info_field="uptime_seconds",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.last_boot,
    ),
    AccessPointSensorDescription(
        key="location",
        translation_key="location",
        info_field="location",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.info.location if s.info else None,
    ),
    AccessPointSensorDescription(
        key="average_client_signal",
        translation_key="average_client_signal",
        client_detail="signal",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda _: None,
        coverage_fn=lambda c: c.average_quality if c else None,
    ),
    AccessPointSensorDescription(
        key="weak_clients",
        translation_key="weak_clients",
        client_detail="signal",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda _: None,
        coverage_fn=lambda c: c.weak_clients if c else 0,
    ),
)


def _area_name(coordinator: AssociationCoordinator, sighting: Sighting) -> str | None:
    """Name of the area of the access point the device is connected to."""
    area = coordinator.access_point_area(sighting.access_point_id)
    return area.name if area else None


def _area_id(coordinator: AssociationCoordinator, sighting: Sighting) -> dict[str, Any]:
    """The area id, stable across renames, for automations."""
    area = coordinator.access_point_area(sighting.access_point_id)
    return {"area_id": area.id if area else None}


@dataclass(frozen=True, kw_only=True)
class TrackedSensorDescription(SensorEntityDescription):
    """A tracked device sensor read from its current sighting."""

    value_fn: Callable[[AssociationCoordinator, Sighting], StateValue]
    # Re-evaluate when areas or device areas change, not only on polls.
    follows_areas: bool = False
    # Attributes; keep to values that change together with the state (recorder rows).
    attrs_fn: Callable[[AssociationCoordinator, Sighting], dict[str, Any]] | None = None
    # Coverage counts of the day: shown while away too (value_fn unused).
    counts_fn: Callable[[DeviceCounts], int] | None = None
    # While the device is in a parking spot, or its presence sensor is on, the value
    # comes from there (it wins): its name for the access point, its area for the area.
    spot_value: Literal["name", "area"] | None = None


TRACKED_SENSORS = (
    TrackedSensorDescription(
        key="access_point",
        translation_key="access_point",
        spot_value="name",
        value_fn=lambda _, s: s.access_point,
    ),
    TrackedSensorDescription(
        key="signal",
        translation_key="signal",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda _, s: s.quality,
        # The raw value on the driver's own scale, e.g. 98 % or -67 dBm.
        attrs_fn=lambda _, s: {"signal": s.signal, "signal_unit": s.signal_unit},
    ),
    TrackedSensorDescription(
        key="area",
        translation_key="area",
        follows_areas=True,
        spot_value="area",
        value_fn=_area_name,
        attrs_fn=_area_id,
    ),
    # Coverage, counted per day (back to 0 at midnight; a decrease is a reset).
    TrackedSensorDescription(
        key="roams_today",
        translation_key="roams_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda _, __: None,
        counts_fn=lambda c: c.roams,
    ),
    TrackedSensorDescription(
        key="drops_today",
        translation_key="drops_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda _, __: None,
        counts_fn=lambda c: c.drops,
    ),
    TrackedSensorDescription(
        key="late_roams_today",
        translation_key="late_roams_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda _, __: None,
        counts_fn=lambda c: c.late_roams,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WifiAssociationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors for each access point and each tracked device."""
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    for ap in coordinator.access_points:
        if not ap.driver.OWN_DEVICE:
            continue
        reported = [
            d
            for d in ACCESS_POINT_SENSORS
            if (d.info_field is None or d.info_field in ap.driver.REPORTS)
            and (d.client_detail is None or d.client_detail in ap.driver.CLIENT_DETAILS)
        ]
        # Sensors created by earlier versions for details this driver never reports.
        for description in ACCESS_POINT_SENSORS:
            if description in reported:
                continue
            unique_id = f"{ap.subentry_id}_{description.key}"
            if entity_id := registry.async_get_entity_id(SENSOR_DOMAIN, DOMAIN, unique_id):
                registry.async_remove(entity_id)
        async_add_entities(
            [AccessPointSensor(coordinator, ap, d) for d in reported],
            config_subentry_id=ap.subentry_id,
        )
    for subentry in entry.get_subentries_of_type(SUBENTRY_TRACKED_DEVICE):
        async_add_entities(
            [TrackedDeviceSensor(coordinator, subentry, d) for d in TRACKED_SENSORS],
            config_subentry_id=subentry.subentry_id,
        )
        if subentry.data.get(CONF_PARKING):
            async_add_entities(
                [ParkingSensor(coordinator, subentry)], config_subentry_id=subentry.subentry_id
            )
        elif entity_id := registry.async_get_entity_id(
            SENSOR_DOMAIN, DOMAIN, f"{subentry.data[CONF_MAC]}_parking"
        ):
            registry.async_remove(entity_id)  # the device no longer uses parking spots
    for subentry in entry.get_subentries_of_type(SUBENTRY_PARKING_SPOT):
        async_add_entities(
            [OccupantSensor(coordinator, subentry)], config_subentry_id=subentry.subentry_id
        )


class AccessPointSensor(CoordinatorEntity[AssociationCoordinator], SensorEntity):
    """A value reported by, or counted from, one access point."""

    _attr_has_entity_name = True
    entity_description: AccessPointSensorDescription

    def __init__(
        self,
        coordinator: AssociationCoordinator,
        ap: ConfiguredAccessPoint,
        description: AccessPointSensorDescription,
    ) -> None:
        """Attach the sensor to the access point's device."""
        super().__init__(coordinator)
        self.entity_description = description
        self._subentry_id = ap.subentry_id
        self._attr_unique_id = f"{ap.subentry_id}_{description.key}"
        self._attr_device_info = access_point_device_info(coordinator, ap)

    @property
    def _ap_state(self) -> AccessPointState | None:
        return self.coordinator.data.access_points.get(self._subentry_id)

    @property
    def available(self) -> bool:
        """Unavailable while the access point can't be read."""
        state = self._ap_state
        return super().available and state is not None and state.available

    @property
    def native_value(self) -> StateValue:
        """Current value."""
        if (coverage_fn := self.entity_description.coverage_fn) is not None:
            return coverage_fn(self.coordinator.access_point_coverage.get(self._subentry_id))
        state = self._ap_state
        return self.entity_description.value_fn(state) if state else None


class TrackedDeviceSensor(CoordinatorEntity[AssociationCoordinator], SensorEntity):
    """Where a tracked device is connected; empty while it is away."""

    _attr_has_entity_name = True
    entity_description: TrackedSensorDescription

    def __init__(
        self,
        coordinator: AssociationCoordinator,
        subentry: ConfigSubentry,
        description: TrackedSensorDescription,
    ) -> None:
        """Attach the sensor to the tracked device."""
        super().__init__(coordinator)
        self.entity_description = description
        self._mac = subentry.data[CONF_MAC]
        self._subentry_id = subentry.subentry_id
        self._attr_unique_id = f"{self._mac}_{description.key}"
        self._attr_device_info = tracked_device_info(subentry)

    def _local(self) -> LocalPlace | None:
        """The parking spot or presence sensor the device is at, if this sensor
        takes its value from it."""
        if self.entity_description.spot_value is None:
            return None
        return self.coordinator.local_place(self._subentry_id)

    @property
    def native_value(self) -> StateValue:
        """Value while the device is home (within the grace period), else unknown.

        Coverage counts of the day are shown whether the device is home or not.
        """
        if (counts_fn := self.entity_description.counts_fn) is not None:
            counts = self.coordinator.coverage.counts.get(self._mac)
            return counts_fn(counts) if counts else 0
        if (local := self._local()) is not None:
            if self.entity_description.spot_value == "name":
                return local.name
            return local.area.name if local.area else None
        sighting = self.coordinator.current_sighting(self._mac)
        return self.entity_description.value_fn(self.coordinator, sighting) if sighting else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Details for the current value, while the device is home."""
        attrs_fn = self.entity_description.attrs_fn
        if attrs_fn is not None and (local := self._local()) is not None:
            return {"area_id": local.area.id if local.area else None}
        sighting = self.coordinator.current_sighting(self._mac)
        if attrs_fn is None or sighting is None:
            return None
        return attrs_fn(self.coordinator, sighting)

    async def async_added_to_hass(self) -> None:
        """Also follow area changes, for sensors that show an area."""
        await super().async_added_to_hass()
        if not self.entity_description.follows_areas:
            return

        @callback
        def _area_changed(_event: Event) -> None:
            self.async_write_ha_state()

        @callback
        def _is_access_point_area_change(data: Mapping[str, Any]) -> bool:
            """Only an area (re)assignment of an access point's device.

            The device registry fires for every device in the house (firmware
            versions, names...), so without this filter each tracked device would
            rewrite its state on all of them.
            """
            if data.get("action") != "update" or "area_id" not in data.get("changes", {}):
                return False
            return data["device_id"] in self.coordinator.access_point_device_ids()

        self.async_on_remove(
            self.hass.bus.async_listen(
                dr.EVENT_DEVICE_REGISTRY_UPDATED,
                _area_changed,
                event_filter=_is_access_point_area_change,
            )
        )
        # Area renames are rare; no filter needed.
        self.async_on_remove(
            self.hass.bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, _area_changed)
        )


class ParkingSensor(CoordinatorEntity[AssociationCoordinator], SensorEntity):
    """Where a car that uses parking spots is: in a spot, outside, away, or home with
    the place unclear (a spot can't tell: its sensor is unavailable, or two cars are
    too close in RSSI)."""

    _attr_has_entity_name = True
    _attr_translation_key = "parking"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [loc.value for loc in Location]

    def __init__(self, coordinator: AssociationCoordinator, subentry: ConfigSubentry) -> None:
        super().__init__(coordinator)
        self._subentry_id = subentry.subentry_id
        self._attr_unique_id = f"{subentry.data[CONF_MAC]}_parking"
        self._attr_device_info = tracked_device_info(subentry)

    @property
    def native_value(self) -> str | None:
        place = self.coordinator.parking.places.get(self._subentry_id)
        return place.location.value if place and place.location else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        spot = self.coordinator.parking.spot_of(self._subentry_id)
        return {"spot": spot.title if spot else None}


# What a spot holds when it isn't one of the cars (enum options next to the car names).
_OCCUPANT_STATES = {SpotKind.EMPTY: "empty", SpotKind.OTHER: "other", SpotKind.UNSURE: "unsure"}


class OccupantSensor(CoordinatorEntity[AssociationCoordinator], SensorEntity):
    """What is in a parking spot: a car's name, empty, something else (wrong height, or
    no car heard near it), or a car but not sure which. Unknown while the occupancy
    sensor is unavailable."""

    _attr_has_entity_name = True
    _attr_translation_key = "occupant"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: AssociationCoordinator, subentry: ConfigSubentry) -> None:
        super().__init__(coordinator)
        self._spot_id = subentry.subentry_id
        self._attr_unique_id = f"{subentry.subentry_id}_occupant"
        self._attr_device_info = parking_spot_device_info(subentry)
        cars = coordinator.parking.cars
        self._attr_options = [*_OCCUPANT_STATES.values(), *(c.title for c in cars.values())]

    @property
    def available(self) -> bool:
        """The spot reads its own sensors: it works while the access points fail."""
        return True

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.parking.occupant(self._spot_id)
        if state is None or state.kind is SpotKind.UNKNOWN:
            return None
        if state.kind is SpotKind.CAR:
            car = self.coordinator.parking.cars.get(state.car_id or "")
            return car.title if car else None
        return _OCCUPANT_STATES[state.kind]
