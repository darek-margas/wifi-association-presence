"""Home Assistant-side data sources for drivers that read through another integration.

Most drivers talk to the access point themselves and only need their settings. A driver
that reads through another Home Assistant integration (UniFi Network) declares the
source it needs in its SOURCE class attribute; this registry says how to build that
source and which of the driver's settings are picked from a list of what the source
knows (e.g. the UniFi access points), so the coordinator and the setup forms never name
a driver. A new source is one entry in SOURCES.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import SelectOptionDict

from wifi_ap_associations import AccessPointDriver
from .unifi_source import HassUnifiSource, access_point_options


@dataclass(frozen=True, slots=True)
class DataSource:
    """How to provide one kind of Home Assistant data source to drivers."""

    # The object handed to the driver's constructor as its second argument.
    create: Callable[[HomeAssistant], Any]
    # Options for the driver's UNIQUE_FIELD, leaving out the values already in use;
    # that setting is then a dropdown instead of a text field.
    unique_field_options: Callable[[HomeAssistant, set[str]], list[SelectOptionDict]]
    # Abort reason (translation key) when there is nothing left to choose.
    no_choices_reason: str


SOURCES: dict[str, DataSource] = {
    "unifi": DataSource(
        create=HassUnifiSource,
        unique_field_options=lambda hass, taken: access_point_options(hass, exclude=taken),
        no_choices_reason="no_unifi_access_points",
    ),
}


def _source(driver_cls: type[AccessPointDriver]) -> DataSource | None:
    return SOURCES[driver_cls.SOURCE] if driver_cls.SOURCE else None


def build_driver(
    hass: HomeAssistant, driver_cls: type[AccessPointDriver], data: dict[str, Any]
) -> AccessPointDriver:
    """Create a driver, handing it the data source it reads through, if it has one."""
    if (source := _source(driver_cls)) is None:
        return driver_cls(data)
    return driver_cls(data, source.create(hass))  # type: ignore[call-arg]


def field_choices(
    hass: HomeAssistant, driver_cls: type[AccessPointDriver], taken: set[str]
) -> tuple[dict[str, list[SelectOptionDict]], str] | None:
    """Settings picked from a list, and the abort reason when the list is empty.

    None for drivers whose settings are all typed in.
    """
    if (source := _source(driver_cls)) is None:
        return None
    options = source.unique_field_options(hass, taken)
    return {driver_cls.UNIQUE_FIELD: options}, source.no_choices_reason
