"""Presence detection from Wi-Fi access point association tables."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.importlib import async_import_module

from .const import DOMAIN, SUBENTRY_TRACKED_DEVICE
from .coordinator import AssociationCoordinator, WifiAssociationConfigEntry
from .entity import access_point_device_info, tracked_device_info

PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: WifiAssociationConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = AssociationCoordinator(hass, entry)
    # Modules that load files on import (pysnmp): off the event loop, before the first poll.
    for module in sorted(
        {m for ap in coordinator.access_points for m in ap.driver.BLOCKING_IMPORTS}
    ):
        await async_import_module(hass, module)
    # Before the first poll: with only such access points it fails and setup retries.
    _update_controller_login_issues(hass, entry, coordinator)
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
        info = access_point_device_info(coordinator, ap)
        if (
            not device_registry.async_get_device_by_identifier(
                (DOMAIN, ap.subentry_id), entry.entry_id
            )
            and (area := _area_of_other_device(hass, ap.driver.device_mac()))
        ):
            # A UniFi or Omada AP that until 0.12 used the device of Home Assistant's
            # UniFi or Omada integration: its own device starts in that device's area.
            info["suggested_area"] = area
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=ap.subentry_id,
            **info,
        )
    for subentry in entry.get_subentries_of_type(SUBENTRY_TRACKED_DEVICE):
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry.subentry_id,
            **tracked_device_info(subentry),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Home Assistant does not unload entries when it stops, so without this a restart
    # would lose the sightings (and visit start times) since the last periodic write.
    async def _async_stop(_event: Event) -> None:
        unsubscribe_stop.clear()  # a one-time listener is gone once it has fired
        await coordinator.async_save_sightings()

    unsubscribe_stop = [hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)]

    @callback
    def _async_remove_stop_listener() -> None:
        for unsubscribe in unsubscribe_stop:
            unsubscribe()

    entry.async_on_unload(_async_remove_stop_listener)
    # Instant arrivals: access points that report changes trigger a poll at once.
    coordinator.async_start_watchers()
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: WifiAssociationConfigEntry
) -> None:
    """Reload when options, access points or tracked devices change."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WifiAssociationConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.async_save_sightings()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        # e.g. a controller session (UniFi, Omada), closed with its last access point
        for ap in entry.runtime_data.access_points:
            await ap.driver.async_close()
    return unloaded


def _area_of_other_device(hass: HomeAssistant, mac: str | None) -> str | None:
    """The area name of another integration's device for this MAC, if it has one."""
    if not mac:
        return None
    devices = dr.async_get(hass).async_get_devices(
        connections={(dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))}
    )
    areas = ar.async_get(hass)
    for device in devices:
        if device.area_id and (area := areas.async_get_area(device.area_id)):
            return area.name
    return None


def _update_controller_login_issues(
    hass: HomeAssistant, entry: WifiAssociationConfigEntry, coordinator: Any
) -> None:
    """A repair notice for each controller access point (UniFi, Omada) saved before it
    logged in itself: it needs the controller's address and login (Reconfigure)."""
    for ap in coordinator.access_points:
        issue_id = f"controller_login_{ap.subentry_id}"
        if ap.driver.LISTS_ACCESS_POINTS and not str(ap.driver.config.get(CONF_HOST) or "").strip():
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="controller_login",
                translation_placeholders={"name": ap.title, "type": ap.driver.NAME},
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
