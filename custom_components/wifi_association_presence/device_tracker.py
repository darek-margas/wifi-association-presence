"""Device trackers: home while the device is associated to any configured AP."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_MAC, CONSIDER_HOME, SUBENTRY_TRACKED_DEVICE
from .coordinator import AssociationCoordinator, WifiAssociationConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WifiAssociationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one tracker per tracked-device subentry."""
    coordinator = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_TRACKED_DEVICE:
            async_add_entities(
                [AssociationTracker(coordinator, subentry)],
                config_subentry_id=subentry_id,
            )


class AssociationTracker(CoordinatorEntity[AssociationCoordinator], ScannerEntity):
    """A device that is home while associated to one of the access points."""

    def __init__(self, coordinator: AssociationCoordinator, subentry: ConfigSubentry) -> None:
        """Track the MAC configured in the subentry."""
        super().__init__(coordinator)
        self._attr_mac_address = subentry.data[CONF_MAC]
        self._attr_name = subentry.title

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enabled: the user explicitly chose to track this device.

        (ScannerEntity otherwise starts disabled unless another integration
        already knows the MAC, which suits routers listing every client.)
        """
        return True

    @property
    def is_connected(self) -> bool:
        """Associated now, or within CONSIDER_HOME of the last sighting."""
        sighting = self.coordinator.data.get(self.mac_address or "")
        return sighting is not None and dt_util.utcnow() - sighting.last_seen < CONSIDER_HOME

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Where the device was last seen."""
        sighting = self.coordinator.data.get(self.mac_address or "")
        if sighting is None:
            return None
        return {
            "access_point": sighting.access_point,
            "ssid": sighting.ssid,
            "band": sighting.band,
            "rssi": sighting.rssi,
            "last_seen": sighting.last_seen.isoformat(),
        }
