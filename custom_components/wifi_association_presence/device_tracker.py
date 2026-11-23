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
        """In a parking spot, or associated now, or within the grace period of the
        last sighting.

        Unknown (None) while no access point is configured: no data source is not
        evidence of having left.
        """
        if self.coordinator.parking.spot_of(self._subentry_id) is not None:
            return True
        if not self.coordinator.has_access_points:
            return None
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

        A car that uses parking spots also has parking_spot: the spot it is identified
        in, else None. While it is in one, it is home in the spot's area, and its visit
        started when it parked (or when Wi-Fi saw it arrive, if earlier).
        """
        parking = self.coordinator.parking
        sighting = self.coordinator.data.sightings.get(self.mac_address or "")
        spot = parking.spot_of(self._subentry_id) if self._parking else None
        if sighting is None and spot is None and not (
            self._parking and parking.left_spot_at(self._subentry_id)
        ):
            return None
        on_wifi = self.coordinator.current_sighting(self.mac_address) is not None
        if spot is not None:
            area = parking.spot_area(spot.subentry_id)
            arrived = parking.in_spot_since(self._subentry_id)
            departed = None
        else:
            area = self.coordinator.access_point_area(sighting.access_point_id) if sighting else None
            arrived = sighting.arrived if on_wifi else None
            # Away: last seen by an access point, or when it left its spot if later.
            left = [t for t in (sighting.last_seen if sighting else None,
                                parking.left_spot_at(self._subentry_id)) if t]
            departed = None if on_wifi else max(left)
        attrs = {
            "access_point": sighting.access_point if sighting else None,
            "area": area.name if area else None,
            "ssid": sighting.ssid if sighting else None,
            "band": sighting.band if sighting else None,
            "arrived_at": arrived.isoformat() if arrived else None,
            "departed_at": departed.isoformat() if departed else None,
        }
        if self._parking:
            attrs["parking_spot"] = spot.title if spot else None
        return attrs
