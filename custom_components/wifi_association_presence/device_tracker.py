"""Device trackers: home while the device is associated to any configured AP."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MAC, SUBENTRY_TRACKED_DEVICE
from .coordinator import AssociationCoordinator, WifiAssociationConfigEntry


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
        self._attr_mac_address = subentry.data[CONF_MAC]
        self._attr_name = subentry.title
        # Home Assistant names the device it links this tracker to after the hostname.
        self._attr_hostname = subentry.title

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enabled: the user explicitly chose to track this device.

        (ScannerEntity otherwise starts disabled unless another integration
        already knows the MAC, which suits routers listing every client.)
        """
        return True

    @property
    def is_connected(self) -> bool | None:
        """Associated now, or within the grace period of the last sighting.

        Unknown (None) while no access point is configured: no data source is not
        evidence of having left.
        """
        if not self.coordinator.has_access_points:
            return None
        return self.coordinator.current_sighting(self.mac_address) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Where the device is, or was last seen, and when it arrived and left.

        Only values that change with the situation are attributes: Home Assistant
        records a row whenever an attribute changes, so a timestamp or signal updated
        every poll would write a row per device per minute. Both times change once per
        arrival or departure:
        - arrived_at: when the current visit started (once away: the last visit);
        - departed_at: when the device left (while home: the end of the previous visit).
        The signal has its own sensor.
        """
        sighting = self.coordinator.data.sightings.get(self.mac_address or "")
        if sighting is None:
            return None
        home = self.coordinator.current_sighting(self.mac_address) is not None
        departed = sighting.departed if home else sighting.last_seen
        area = self.coordinator.access_point_area(sighting.access_point_id)
        return {
            "access_point": sighting.access_point,
            "area": area.name if area else None,
            "ssid": sighting.ssid,
            "band": sighting.band,
            "arrived_at": sighting.arrived.isoformat() if sighting.arrived else None,
            "departed_at": departed.isoformat() if departed else None,
        }
