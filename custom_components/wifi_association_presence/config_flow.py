"""UI setup: one hub entry, with access points and tracked devices as subentries."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .ap_drivers import (
    DRIVERS,
    AccessPointAuthError,
    AccessPointDriver,
    AccessPointError,
    normalize_mac,
)
from .const import (
    CONF_DRIVER,
    CONF_MAC,
    DOMAIN,
    LOGGER,
    SUBENTRY_ACCESS_POINT,
    SUBENTRY_TRACKED_DEVICE,
)

CONF_NAME = "name"


class WifiAssociationPresenceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single hub entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then create the hub; APs and devices are added as subentries."""
        if user_input is not None:
            return self.async_create_entry(title="Wi-Fi association presence", data={})
        return self.async_show_form(step_id="user")

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Access points and tracked devices are added from the integration page."""
        return {
            SUBENTRY_ACCESS_POINT: AccessPointSubentryFlow,
            SUBENTRY_TRACKED_DEVICE: TrackedDeviceSubentryFlow,
        }


class AccessPointSubentryFlow(ConfigSubentryFlow):
    """Add an access point: choose its type, then that type's settings."""

    _driver_type: str

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the access point type."""
        if user_input is not None:
            self._driver_type = user_input[CONF_DRIVER]
            return await self.async_step_details()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DRIVER): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=type_, label=cls.NAME)
                                for type_, cls in DRIVERS.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_details(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for the settings of the chosen type and test them on the AP."""
        driver_cls = DRIVERS[self._driver_type]
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input.get("host")
            if any(
                sub.data.get("host") == host
                for sub in self._get_entry().get_subentries_of_type(SUBENTRY_ACCESS_POINT)
            ):
                return self.async_abort(reason="already_configured")
            data = {CONF_DRIVER: self._driver_type, **user_input}
            errors = await _async_test_access_point(driver_cls(data))
            if not errors:
                return self.async_create_entry(title=host or driver_cls.NAME, data=data)

        schema: dict[Any, Any] = {}
        for field in driver_cls.FIELDS:
            key = (
                vol.Required(field.key, default=field.default)
                if field.default is not None
                else vol.Required(field.key)
            )
            if field.secret:
                schema[key] = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
            elif isinstance(field.default, int):
                schema[key] = vol.All(
                    NumberSelector(
                        NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Coerce(int),
                )
            else:
                schema[key] = TextSelector()
        return self.async_show_form(
            step_id="details",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), user_input or {}
            ),
            errors=errors,
            description_placeholders={"type": driver_cls.NAME},
        )


async def _async_test_access_point(driver: AccessPointDriver) -> dict[str, str]:
    """Read the AP once; map failures to form errors."""
    try:
        await driver.async_get_associated_clients()
    except AccessPointAuthError:
        return {"base": "invalid_auth"}
    except AccessPointError as err:
        LOGGER.debug("Access point test failed: %s", err)
        return {"base": "cannot_connect"}
    return {}


class TrackedDeviceSubentryFlow(ConfigSubentryFlow):
    """Add a device to track by its Wi-Fi MAC address."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick a currently associated MAC (or type one) and name it."""
        entry = self._get_entry()
        tracked = {
            sub.data[CONF_MAC]
            for sub in entry.get_subentries_of_type(SUBENTRY_TRACKED_DEVICE)
        }
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                mac = normalize_mac(user_input[CONF_MAC])
            except ValueError:
                errors[CONF_MAC] = "invalid_mac"
            else:
                if mac in tracked:
                    return self.async_abort(reason="already_configured")
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data={CONF_MAC: mac}, unique_id=mac
                )

        seen = getattr(entry, "runtime_data", None)
        options = [
            SelectOptionDict(
                value=mac,
                label=f"{mac}  ({sighting.access_point}, {sighting.band or '?'}, "
                f"{sighting.ssid or '?'}, rssi {sighting.rssi if sighting.rssi is not None else '?'})",
            )
            for mac, sighting in sorted((seen.data or {}).items() if seen else [])
            if mac not in tracked
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_MAC): SelectSelector(
                            SelectSelectorConfig(
                                options=options,
                                custom_value=True,
                                mode=SelectSelectorMode.DROPDOWN,
                            )
                        ),
                        vol.Required(CONF_NAME): TextSelector(),
                    }
                ),
                user_input or {},
            ),
            errors=errors,
        )
