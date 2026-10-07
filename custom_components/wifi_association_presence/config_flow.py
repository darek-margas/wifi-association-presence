"""UI setup: one hub entry, with access points and tracked devices as subentries.

Tracked devices are keyed only by MAC and are independent of the access points, so
access points can be removed, replaced or switched to another driver type without
touching them; with no access point configured the trackers report "unknown".
"""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
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
    CONF_CONSIDER_HOME,
    CONF_DRIVER,
    CONF_MAC,
    CONF_MODEL,
    CONF_NAME,
    DEFAULT_CONSIDER_HOME,
    DOMAIN,
    LOGGER,
    SUBENTRY_ACCESS_POINT,
    SUBENTRY_TRACKED_DEVICE,
)


TEST_TIMEOUT = 45


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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Hub options."""
        return WifiAssociationPresenceOptionsFlow()

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


class WifiAssociationPresenceOptionsFlow(OptionsFlow):
    """Grace period before a device that disappeared counts as away."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_CONSIDER_HOME): vol.All(
                            NumberSelector(
                                NumberSelectorConfig(
                                    min=0,
                                    max=3600,
                                    step=10,
                                    unit_of_measurement="s",
                                    mode=NumberSelectorMode.BOX,
                                )
                            ),
                            vol.Coerce(int),
                        )
                    }
                ),
                {
                    CONF_CONSIDER_HOME: self.config_entry.options.get(
                        CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME
                    )
                },
            ),
        )


class AccessPointSubentryFlow(ConfigSubentryFlow):
    """Add or edit an access point: its type, a name and that type's settings."""

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
        """Ask for the name and settings of the chosen type, and test them."""
        driver_cls = DRIVERS[self._driver_type]
        errors: dict[str, str] = {}
        if user_input is not None:
            name, data = _split_name(user_input, driver_cls)
            if self._host_in_use(data.get("host")):
                return self.async_abort(reason="already_configured")
            data = {CONF_DRIVER: self._driver_type, **data}
            errors, reported_name = await _async_test_access_point(driver_cls(data))
            if not errors:
                return self.async_create_entry(
                    title=name or reported_name or data.get("host") or driver_cls.NAME,
                    data=data,
                )
        return self.async_show_form(
            step_id="details",
            data_schema=self.add_suggested_values_to_schema(
                _access_point_schema(driver_cls, editing=False), user_input or {}
            ),
            errors=errors,
            description_placeholders={"type": driver_cls.NAME},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit name and settings; a blank secret keeps the stored one."""
        subentry = self._get_reconfigure_subentry()
        driver_cls = DRIVERS.get(subentry.data[CONF_DRIVER])
        if driver_cls is None:
            return self.async_abort(reason="unknown_driver")
        errors: dict[str, str] = {}
        if user_input is not None:
            name, changes = _split_name(user_input, driver_cls)
            if self._host_in_use(changes.get("host"), except_id=subentry.subentry_id):
                return self.async_abort(reason="already_configured")
            data = {**subentry.data, **{k: v for k, v in changes.items() if v not in (None, "")}}
            errors, reported_name = await _async_test_access_point(driver_cls(data))
            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=name or reported_name or data.get("host") or driver_cls.NAME,
                    data=data,
                )
        suggested = {
            CONF_NAME: subentry.title,
            CONF_MODEL: subentry.data.get(CONF_MODEL),
            **{f.key: subentry.data.get(f.key) for f in driver_cls.FIELDS if not f.secret},
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _access_point_schema(driver_cls, editing=True), user_input or suggested
            ),
            errors=errors,
            description_placeholders={"type": driver_cls.NAME},
        )

    def _host_in_use(self, host: Any, except_id: str | None = None) -> bool:
        """Whether another access point already uses this host."""
        return bool(host) and any(
            sub.data.get("host") == host and sub.subentry_id != except_id
            for sub in self._get_entry().get_subentries_of_type(SUBENTRY_ACCESS_POINT)
        )


def _access_point_schema(driver_cls: type[AccessPointDriver], editing: bool) -> vol.Schema:
    """Form fields: an optional name, then the driver's own settings."""
    schema: dict[Any, Any] = {
        vol.Optional(CONF_NAME): TextSelector(),
        # Shown on the device; drivers may also report it themselves.
        vol.Optional(CONF_MODEL): TextSelector(),
    }
    for field in driver_cls.FIELDS:
        if field.secret:
            # When editing, leaving the secret blank keeps the stored value.
            key = vol.Optional(field.key) if editing else vol.Required(field.key)
            schema[key] = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
            continue
        key = (
            vol.Required(field.key, default=field.default)
            if field.default is not None
            else vol.Required(field.key)
        )
        if isinstance(field.default, int):
            schema[key] = vol.All(
                NumberSelector(
                    NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
                ),
                vol.Coerce(int),
            )
        else:
            schema[key] = TextSelector()
    return vol.Schema(schema)


def _split_name(
    user_input: dict[str, Any], driver_cls: type[AccessPointDriver]
) -> tuple[str | None, dict[str, Any]]:
    """Separate the display name (subentry title) from the driver settings.

    Text settings are stripped of surrounding spaces (a pasted " 192.168.1.10" would
    otherwise fail to connect); secrets are kept exactly as typed.
    """
    secrets = {field.key for field in driver_cls.FIELDS if field.secret}
    data = {
        key: value.strip() if isinstance(value, str) and key not in secrets else value
        for key, value in user_input.items()
    }
    name = (data.pop(CONF_NAME, None) or "").strip() or None
    return name, data


async def _async_test_access_point(
    driver: AccessPointDriver,
) -> tuple[dict[str, str], str | None]:
    """Read the AP once; return form errors and the name the AP reports, if any."""
    try:
        result = await asyncio.wait_for(driver.async_poll(), TEST_TIMEOUT)
    except AccessPointAuthError:
        return {"base": "invalid_auth"}, None
    except (AccessPointError, TimeoutError) as err:
        LOGGER.debug("Access point test failed: %s", err)
        return {"base": "cannot_connect"}, None
    except Exception:
        LOGGER.exception("Unexpected error testing the access point")
        return {"base": "unknown"}, None
    return {}, result.info.name if result.info else None


class TrackedDeviceSubentryFlow(ConfigSubentryFlow):
    """Add a device to track by its Wi-Fi MAC address, or rename it."""

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
                    title=user_input[CONF_NAME].strip(), data={CONF_MAC: mac}, unique_id=mac
                )

        coordinator = getattr(entry, "runtime_data", None)
        seen = coordinator.data.sightings if coordinator and coordinator.data else {}
        options = [
            SelectOptionDict(
                value=mac,
                label=f"{mac}  ({sighting.access_point}, {sighting.band or '?'}, "
                f"{sighting.ssid or '?'}, rssi "
                f"{sighting.rssi if sighting.rssi is not None else '?'})",
            )
            for mac, sighting in sorted(seen.items())
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Rename the device (the MAC identifies it and stays fixed)."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=user_input[CONF_NAME].strip()
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_NAME): TextSelector()}),
                {CONF_NAME: subentry.title},
            ),
            description_placeholders={"mac": subentry.data[CONF_MAC]},
        )
