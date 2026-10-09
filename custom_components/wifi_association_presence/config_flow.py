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
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
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
from homeassistant.util import dt as dt_util

from wifi_ap_associations import (
    DRIVERS,
    AccessPointAuthError,
    AccessPointDriver,
    AccessPointError,
    normalize_mac,
)
from wifi_ap_associations.collect import PROFILES as COLLECT_PROFILES
from wifi_ap_associations.collect import async_collect_ssh_report
from .const import (
    COLLECT_TIMEOUT,
    CONF_COMMANDS,
    CONF_CONSIDER_HOME,
    CONF_DRIVER,
    CONF_LEGACY_SSH,
    CONF_MAC,
    CONF_MODEL,
    CONF_NAME,
    CONF_PROFILE,
    DEFAULT_CONSIDER_HOME,
    DOMAIN,
    LOGGER,
    SUBENTRY_ACCESS_POINT,
    SUBENTRY_REPORT,
    SUBENTRY_TRACKED_DEVICE,
)
from .coordinator import AssociationCoordinator, user_named
from .diagnostics import store_report
from .presence import Sighting
from .sources import build_driver, field_choices


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
        """Buttons on the integration page: add an access point or a tracked device, or
        collect a report from an access point that isn't supported yet (that one never
        creates a subentry)."""
        return {
            SUBENTRY_ACCESS_POINT: AccessPointSubentryFlow,
            SUBENTRY_TRACKED_DEVICE: TrackedDeviceSubentryFlow,
            SUBENTRY_REPORT: CollectReportSubentryFlow,
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


class CollectReportSubentryFlow(ConfigSubentryFlow):
    """Collect a report from an access point that isn't supported yet.

    Started by the "Collect access point report" button on the integration page. It
    creates no subentry: the redacted report is kept in memory for Download
    diagnostics, and the password is used for this one login only.
    """

    def __init__(self) -> None:
        """Start without a collection running."""
        self._collect_input: dict[str, Any] = {}
        self._collect_task: asyncio.Task[str | None] | None = None
        self._collect_error: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask how to log in to the access point to collect a report from."""
        if user_input is not None:
            self._collect_input = user_input
            self._collect_error = None
            return await self.async_step_collecting()
        errors = {"base": self._collect_error} if self._collect_error else {}
        suggested = {k: v for k, v in self._collect_input.items() if k != CONF_PASSWORD}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): TextSelector(),
                        vol.Required(CONF_PORT, default=22): vol.All(
                            NumberSelector(
                                NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
                            ),
                            vol.Coerce(int),
                        ),
                        vol.Required(CONF_USERNAME, default="admin"): TextSelector(),
                        vol.Required(CONF_PASSWORD): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.PASSWORD)
                        ),
                        vol.Required(CONF_PROFILE, default="generic"): SelectSelector(
                            SelectSelectorConfig(
                                options=list(COLLECT_PROFILES),
                                mode=SelectSelectorMode.DROPDOWN,
                                translation_key=CONF_PROFILE,
                            )
                        ),
                        vol.Optional(CONF_COMMANDS, default=""): TextSelector(
                            TextSelectorConfig(multiline=True)
                        ),
                        vol.Required(CONF_LEGACY_SSH, default=False): BooleanSelector(),
                    }
                ),
                suggested,
            ),
            errors=errors,
        )

    async def async_step_collecting(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Run the collection, showing progress (it can take a minute)."""
        if self._collect_task is None:
            self._collect_task = self.hass.async_create_task(
                self._async_collect(self._collect_input)
            )
        if not self._collect_task.done():
            return self.async_show_progress(
                step_id="collecting",
                progress_action="collecting",
                progress_task=self._collect_task,
                description_placeholders={"host": self._collect_input[CONF_HOST]},
            )
        self._collect_error = self._collect_task.result()
        self._collect_task = None
        return self.async_show_progress_done(
            next_step_id="user" if self._collect_error else "collect_done"
        )

    async def async_step_collect_done(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Point to the diagnostics download; nothing is added or changed."""
        return self.async_abort(reason="report_ready")

    async def _async_collect(self, data: dict[str, Any]) -> str | None:
        """Collect and keep the report for diagnostics; return an error key, or None."""
        commands = [line.strip() for line in data.get(CONF_COMMANDS, "").splitlines()]
        try:
            async with asyncio.timeout(COLLECT_TIMEOUT):
                report = await async_collect_ssh_report(
                    data[CONF_HOST].strip(),
                    data[CONF_USERNAME],
                    data[CONF_PASSWORD],
                    port=data[CONF_PORT],
                    profile=data[CONF_PROFILE],
                    commands=[c for c in commands if c],
                    legacy_ssh=data[CONF_LEGACY_SSH],
                )
        except AccessPointAuthError:
            return "invalid_auth"
        except AccessPointError as err:
            LOGGER.debug("Collecting a report from %s failed: %s", data[CONF_HOST], err)
            return "legacy_ssh" if "legacy SSH" in str(err) else "cannot_connect"
        except TimeoutError:
            return "timeout"
        except Exception:  # never leave the flow stuck on an unexpected error
            LOGGER.exception("Unexpected error collecting a report from %s", data[CONF_HOST])
            return "unknown"
        store_report(
            self.hass,
            host=data[CONF_HOST].strip(),
            profile=data[CONF_PROFILE],
            legacy_ssh=data[CONF_LEGACY_SSH],
            report=report,
        )
        return None


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
                                SelectOptionDict(
                                    value=type_,
                                    label=f"{cls.NAME} (experimental)"
                                    if cls.EXPERIMENTAL
                                    else cls.NAME,
                                )
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
            if self._in_use(driver_cls, data.get(driver_cls.UNIQUE_FIELD)):
                return self.async_abort(reason="already_configured")
            # The name is stored explicitly, so "user named it" isn't guessed later.
            data = {CONF_DRIVER: self._driver_type, **data, CONF_NAME: name or ""}
            errors, reported_name = await _async_build_and_test(self.hass, driver_cls, data)
            if not errors:
                return self.async_create_entry(
                    title=name
                    or reported_name
                    or data.get(driver_cls.UNIQUE_FIELD)
                    or driver_cls.NAME,
                    data=data,
                )
        picked = self._field_choices(driver_cls)
        choices = picked[0] if picked else None
        if picked and not any(choices.values()):
            # e.g. no UniFi access point left to add
            return self.async_abort(reason=picked[1])
        return self.async_show_form(
            step_id="details",
            data_schema=self.add_suggested_values_to_schema(
                _access_point_schema(driver_cls, editing=False, choices=choices),
                user_input or {},
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
            if self._in_use(
                driver_cls,
                changes.get(driver_cls.UNIQUE_FIELD),
                except_id=subentry.subentry_id,
            ):
                return self.async_abort(reason="already_configured")
            data = {
                **subentry.data,
                **{k: v for k, v in changes.items() if v not in (None, "")},
                CONF_NAME: name or "",
            }
            errors, reported_name = await _async_build_and_test(self.hass, driver_cls, data)
            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=name
                    or reported_name
                    or data.get(driver_cls.UNIQUE_FIELD)
                    or driver_cls.NAME,
                    data=data,
                )
        picked = self._field_choices(driver_cls, subentry.subentry_id)
        suggested = {
            CONF_NAME: subentry.title if user_named(subentry) else None,
            CONF_MODEL: subentry.data.get(CONF_MODEL),
            **{f.key: subentry.data.get(f.key) for f in driver_cls.FIELDS if not f.secret},
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _access_point_schema(
                    driver_cls,
                    editing=True,
                    choices=picked[0] if picked else None,
                ),
                user_input or suggested,
            ),
            errors=errors,
            description_placeholders={"type": driver_cls.NAME},
        )

    def _in_use(
        self, driver_cls: type[AccessPointDriver], value: Any, except_id: str | None = None
    ) -> bool:
        """Whether another access point already uses this host (or AP MAC)."""
        return bool(value) and any(
            sub.data.get(driver_cls.UNIQUE_FIELD) == value and sub.subentry_id != except_id
            for sub in self._get_entry().get_subentries_of_type(SUBENTRY_ACCESS_POINT)
        )

    def _field_choices(
        self, driver_cls: type[AccessPointDriver], except_id: str | None = None
    ) -> tuple[dict[str, list[SelectOptionDict]], str] | None:
        """Settings picked from a list rather than typed, and the abort reason when
        nothing is left to pick (None if the driver's settings are all typed in).

        Values used by other access points are left out of the lists, e.g. UniFi
        access points already added.
        """
        taken = {
            value
            for sub in self._get_entry().get_subentries_of_type(SUBENTRY_ACCESS_POINT)
            if sub.subentry_id != except_id
            and (value := sub.data.get(driver_cls.UNIQUE_FIELD)) is not None
        }
        return field_choices(self.hass, driver_cls, taken)


def _access_point_schema(
    driver_cls: type[AccessPointDriver],
    editing: bool,
    choices: dict[str, list[SelectOptionDict]] | None = None,
) -> vol.Schema:
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
        if choices and field.key in choices:
            schema[vol.Required(field.key)] = SelectSelector(
                SelectSelectorConfig(
                    options=choices[field.key], mode=SelectSelectorMode.DROPDOWN
                )
            )
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


async def _async_build_and_test(
    hass: HomeAssistant, driver_cls: type[AccessPointDriver], data: dict[str, Any]
) -> tuple[dict[str, str], str | None]:
    """Create the driver and read the AP once; return form errors and its reported name.

    A value the driver rejects outright (e.g. a malformed MAC submitted through the
    API, which the dropdown prevents in the UI) is reported on its field.
    """
    try:
        driver = build_driver(hass, driver_cls, data)
    except (KeyError, ValueError):
        return {driver_cls.UNIQUE_FIELD: "invalid_value"}, None
    return await _async_test_access_point(driver)


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


def _sighting_label(mac: str, sighting: Sighting, coordinator: AssociationCoordinator) -> str:
    """ "MAC  (AP, band, SSID, signal 98%)", plus when it was seen if not connected now."""
    signal = f"{sighting.signal}{sighting.signal_unit}" if sighting.signal is not None else "?"
    label = (
        f"{mac}  ({sighting.access_point}, {sighting.band or '?'}, "
        f"{sighting.ssid or '?'}, signal {signal})"
    )
    if coordinator.current_sighting(mac) is None:
        last_seen = dt_util.as_local(sighting.last_seen).strftime("%d %b %H:%M")
        label += f", last seen {last_seen}"
    return label


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
        # Most recently seen first; devices not connected now (e.g. a sleeping car)
        # are still offered, with when they were last seen.
        options = [
            SelectOptionDict(value=mac, label=_sighting_label(mac, sighting, coordinator))
            for mac, sighting in sorted(
                seen.items(), key=lambda item: item[1].last_seen, reverse=True
            )
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
