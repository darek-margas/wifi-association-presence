"""Buttons: restart an access point (for drivers that can, e.g. D-Link DAP)."""

from __future__ import annotations

import asyncio

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from wifi_ap_associations import AccessPointError

from .const import DOMAIN, LOGGER
from .coordinator import AssociationCoordinator, ConfiguredAccessPoint, WifiAssociationConfigEntry
from .entity import access_point_device_info

# Login, a few pages, and the command (the driver's own requests time out at 30 s).
RESTART_TIMEOUT = 45


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WifiAssociationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """A Restart button on each access point device whose driver can restart it."""
    coordinator = entry.runtime_data
    for ap in coordinator.access_points:
        if ap.driver.OWN_DEVICE and ap.driver.RESTARTS:
            async_add_entities(
                [AccessPointRestartButton(coordinator, ap)], config_subentry_id=ap.subentry_id
            )


class AccessPointRestartButton(CoordinatorEntity[AssociationCoordinator], ButtonEntity):
    """Reboot the access point. Its devices ride out the minute or two it is gone on
    the grace period, as for any access point that can't be read."""

    _attr_has_entity_name = True
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: AssociationCoordinator, ap: ConfiguredAccessPoint) -> None:
        super().__init__(coordinator)
        self._ap = ap
        self._attr_unique_id = f"{ap.subentry_id}_restart"
        self._attr_device_info = access_point_device_info(coordinator, ap)

    @property
    def available(self) -> bool:
        """Pressable even while the AP can't be read: that may be why it's pressed."""
        return True

    async def async_press(self) -> None:
        LOGGER.info("Restarting access point %s", self._ap.title)
        try:
            async with asyncio.timeout(RESTART_TIMEOUT):
                await self._ap.driver.async_restart()
        except (AccessPointError, TimeoutError) as err:
            reason = str(err) or "timeout"
            # In the log too, with the reason, not only in the dialog.
            LOGGER.error("Restarting access point %s failed: %s", self._ap.title, reason)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="restart_failed",
                translation_placeholders={"name": self._ap.title, "error": reason},
            ) from err
        LOGGER.info("Access point %s took the restart; back in a minute or two", self._ap.title)
