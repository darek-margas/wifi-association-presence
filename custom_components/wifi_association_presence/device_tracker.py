"""Device trackers: home while the device is associated to any configured AP."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MAC, CONF_PARKING, SUBENTRY_TRACKED_DEVICE
from .coordinator import AssociationCoordinator, WifiAssociationConfigEntry
from .entity import tracked_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WifiAssociationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one tracker per tracked-device subentry."""
    coordinator = entry.runtime_data
    for subentry in entry.get_subentries_of_type(SUBENTRY_TRACKED_DEVICE):
        async_add_entities(
            [AssociationTracker(coordinator, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class AssociationTracker(CoordinatorEntity[AssociationCoordinator], ScannerEntity):
    """A device that is home while associated to one of the access points."""

    # Only selects the icons in icons.json; the name comes from the subentry.
    _attr_translation_key = "tracker"

    def __init__(self, coordinator: AssociationCoordinator, subentry: ConfigSubentry) -> None:
        """Track the MAC configured in the subentry."""
        super().__init__(coordinator)
        self._subentry_id = subentry.subentry_id
        self._parking = bool(subentry.data.get(CONF_PARKING))
        self._attr_mac_address = subentry.data[CONF_MAC]
        self._attr_name = subentry.title
        self._attr_hostname = subentry.title
        # The same device as the device's sensors (found by its MAC). Home Assistant
        # 2026.11 warns about a scanner tracker without device info, and 2027.11 will
        # stop linking one to the device by MAC on its own. Earlier versions ignore it
        # and link the tracker by MAC themselves.
        self._attr_device_info = tracked_device_info(subentry)

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enabled: the user explicitly chose to track this device.

        (ScannerEntity otherwise starts disabled unless another integration
        already knows the MAC, which suits routers listing every client.)
        """
        return True

    @property
    def is_connected(self) -> bool | None:
        """In a parking spot, or its presence sensor is on, or associated now, or
        within the grace period of the last sighting.

        Unknown (None) while no access point is configured: no data source is not
        evidence of having left (unless its presence sensor says off).
        """
        if self.coordinator.local_place(self._subentry_id) is not None:
            return True
        if not self.coordinator.has_access_points:
            return False if self.coordinator.presence_sensors.is_on(self._subentry_id) is False else None
        return self.coordinator.current_sighting(self.mac_address) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Where the device is, or was last seen, and when it arrived and left.

        Only values that change with the situation are attributes: Home Assistant
        records a row whenever an attribute changes, so a timestamp or signal updated
        every poll would write a row per device per minute. Exactly one of the times is
        set, matching the state, so automations can trigger on it appearing:
        - home: arrived_at = when this visit started, departed_at = None;
        - away: arrived_at = None, departed_at = when it was last seen.
        The signal has its own sensor.

        A parking spot, else the device's presence sensor while on, stands in for the
        access point: it is home in that spot's (or sensor's) area, with its name as
        access_point; ssid and band stay the Wi-Fi's. Its visit started at the
        earliest of the Wi-Fi arrival and the spot or sensor. A car that uses parking
        spots also has parking_spot: the spot it is identified in, else None.
        """
        parking = self.coordinator.parking
        sighting = self.coordinator.data.sightings.get(self.mac_address or "")
        spot = parking.spot_of(self._subentry_id) if self._parking else None
        local = self.coordinator.local_place(self._subentry_id)
        on_wifi = self.coordinator.current_sighting(self.mac_address) is not None
        # Away: last seen by an access point, left its spot, or its sensor turned off.
        left = [t for t in (sighting.last_seen if sighting else None,
                            parking.left_spot_at(self._subentry_id),
                            self.coordinator.presence_sensors.off_since(self._subentry_id)) if t]
        if sighting is None and local is None and not left:
            return None
        if local is not None:
            area = local.area
            starts = [t for t in (local.since, sighting.arrived if on_wifi else None) if t]
            arrived = min(starts) if starts else None
            departed = None
        else:
            area = self.coordinator.access_point_area(sighting.access_point_id) if sighting else None
            arrived = sighting.arrived if on_wifi else None
            departed = None if on_wifi else max(left)
        attrs = {
            "access_point": local.name if local else (sighting.access_point if sighting else None),
            "area": area.name if area else None,
            "ssid": sighting.ssid if sighting else None,
            "band": sighting.band if sighting else None,
            "arrived_at": arrived.isoformat() if arrived else None,
            "departed_at": departed.isoformat() if departed else None,
        }
        if self._parking:
            attrs["parking_spot"] = spot.title if spot else None
        return attrs
