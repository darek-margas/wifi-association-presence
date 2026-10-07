"""Device descriptions shared by the platforms."""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo

from .const import CONF_MAC, CONF_MODEL, DOMAIN
from .coordinator import AssociationCoordinator, ConfiguredAccessPoint


def access_point_device_info(
    coordinator: AssociationCoordinator, ap: ConfiguredAccessPoint
) -> DeviceInfo:
    """The access point as a device, with what it reports about itself."""
    state = coordinator.data.access_points.get(ap.subentry_id) if coordinator.data else None
    info = state.info if state else None
    host = ap.driver.config.get("host")
    return DeviceInfo(
        identifiers={(DOMAIN, ap.subentry_id)},
        name=ap.title,
        manufacturer=ap.driver.MANUFACTURER,
        model=(info.model if info else None) or ap.driver.config.get(CONF_MODEL) or None,
        sw_version=info.firmware if info else None,
        hw_version=info.hardware if info else None,
        configuration_url=f"http://{host}" if host else None,
    )


def tracked_device_info(subentry: ConfigSubentry) -> DeviceInfo:
    """The tracked device, identified by its MAC.

    Its device_tracker attaches to this device automatically, because Home
    Assistant links scanner trackers to the device registered with their MAC.
    """
    return DeviceInfo(
        connections={(CONNECTION_NETWORK_MAC, subentry.data[CONF_MAC])},
        name=subentry.title,
    )
