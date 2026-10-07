"""Presence detection from Wi-Fi access point association tables."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, SUBENTRY_TRACKED_DEVICE
from .coordinator import AssociationCoordinator, WifiAssociationConfigEntry
from .entity import access_point_device_info, tracked_device_info

PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: WifiAssociationConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = AssociationCoordinator(hass, entry)
    await coordinator.async_restore_sightings()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Register devices before the platforms, so each device_tracker finds the device
    # carrying its MAC and attaches to it on the first start.
    device_registry = dr.async_get(hass)
    for ap in coordinator.access_points:
        if not ap.driver.OWN_DEVICE:
            # Left over from versions that gave every AP a device: it only ever
            # belonged to this subentry, so dropping it loses nothing.
            if stale := device_registry.async_get_device_by_identifier(
                (DOMAIN, ap.subentry_id), entry.entry_id
            ):
                device_registry.async_remove_device(stale.id)
            continue
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=ap.subentry_id,
            **access_point_device_info(coordinator, ap),
        )
    for subentry in entry.get_subentries_of_type(SUBENTRY_TRACKED_DEVICE):
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry.subentry_id,
            **tracked_device_info(subentry),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: WifiAssociationConfigEntry
) -> None:
    """Reload when options, access points or tracked devices change."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WifiAssociationConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.async_save_sightings()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
