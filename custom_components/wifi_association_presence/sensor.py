"""Sensors: access point details and where each tracked device is connected."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MAC, SUBENTRY_TRACKED_DEVICE
from .coordinator import (
    AccessPointState,
    AssociationCoordinator,
    ConfiguredAccessPoint,
    Sighting,
    WifiAssociationConfigEntry,
)
from .entity import access_point_device_info, tracked_device_info

type StateValue = str | int | datetime | None


@dataclass(frozen=True, kw_only=True)
class AccessPointSensorDescription(SensorEntityDescription):
    """An access point sensor and how to read it from the AP's state."""

    value_fn: Callable[[AccessPointState], StateValue]


ACCESS_POINT_SENSORS = (
    AccessPointSensorDescription(
        key="clients_2_4ghz",
        translation_key="clients_2_4ghz",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.clients_by_band.get("2.4GHz", 0),
    ),
    AccessPointSensorDescription(
        key="clients_5ghz",
        translation_key="clients_5ghz",
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
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.info.cpu_percent if s.info else None,
    ),
    AccessPointSensorDescription(
        key="memory",
        translation_key="memory",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.info.memory_percent if s.info else None,
    ),
    AccessPointSensorDescription(
        key="last_boot",
        translation_key="last_boot",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.last_boot,
    ),
    AccessPointSensorDescription(
        key="location",
        translation_key="location",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.info.location if s.info else None,
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


TRACKED_SENSORS = (
    TrackedSensorDescription(
        key="access_point",
        translation_key="access_point",
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
        value_fn=_area_name,
        attrs_fn=_area_id,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WifiAssociationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors for each access point and each tracked device."""
    coordinator = entry.runtime_data
    for ap in coordinator.access_points:
        if not ap.driver.OWN_DEVICE:
            continue
        async_add_entities(
            [AccessPointSensor(coordinator, ap, d) for d in ACCESS_POINT_SENSORS],
            config_subentry_id=ap.subentry_id,
        )
    for subentry in entry.get_subentries_of_type(SUBENTRY_TRACKED_DEVICE):
        async_add_entities(
            [TrackedDeviceSensor(coordinator, subentry, d) for d in TRACKED_SENSORS],
            config_subentry_id=subentry.subentry_id,
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
        self._attr_unique_id = f"{self._mac}_{description.key}"
        self._attr_device_info = tracked_device_info(subentry)

    @property
    def native_value(self) -> StateValue:
        """Value while the device is home (within the grace period), else unknown."""
        sighting = self.coordinator.current_sighting(self._mac)
        return self.entity_description.value_fn(self.coordinator, sighting) if sighting else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Details for the current value, while the device is home."""
        attrs_fn = self.entity_description.attrs_fn
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
