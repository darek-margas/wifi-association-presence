"""Access point drivers: read the Wi-Fi association table of an access point.

An associated client is one that has joined (authenticated to) the AP's wireless
network, as listed by the AP itself. That is a stronger presence signal than a MAC
seen in an ARP table or a switch's forwarding table: a phone stays associated while
it is idle, when it sends no traffic for minutes.

This package deliberately has no Home Assistant imports, so it can later be split
into a standalone library. Each AP model or access method is one driver class,
registered by its TYPE string; the integration offers the registered types in its UI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar


class AccessPointError(Exception):
    """Communication with an access point failed."""


class AccessPointAuthError(AccessPointError):
    """The access point rejected the credentials."""


@dataclass(frozen=True, slots=True)
class AssociatedClient:
    """A wireless client currently associated to an access point."""

    mac: str
    """Upper-case, colon-separated MAC address."""
    ssid: str | None = None
    """SSID as the AP reports it (may be an index label rather than the name)."""
    band: str | None = None
    """Radio band, e.g. "2.4GHz" or "5GHz"."""
    signal: int | None = None
    """Signal strength on the driver's scale, see AccessPointDriver.SIGNAL_UNIT."""
    connected_seconds: int | None = None
    """Seconds since the client associated."""


@dataclass(frozen=True, slots=True)
class AccessPointInfo:
    """What an access point reports about itself (any field may be unknown)."""

    name: str | None = None
    location: str | None = None
    model: str | None = None
    firmware: str | None = None
    hardware: str | None = None
    uptime_seconds: int | None = None
    cpu_percent: int | None = None
    memory_percent: int | None = None


@dataclass(frozen=True, slots=True)
class PollResult:
    """One read of an access point."""

    clients: list[AssociatedClient]
    info: AccessPointInfo | None = None


@dataclass(frozen=True, slots=True)
class DriverField:
    """A setting a driver needs, e.g. host or password."""

    key: str
    secret: bool = False
    default: str | int | None = None


class AccessPointDriver(ABC):
    """Base class for an access point driver."""

    TYPE: ClassVar[str]
    """Stable identifier stored in configuration. Never change it once released."""
    NAME: ClassVar[str]
    """Human readable name shown when choosing the AP type."""
    MANUFACTURER: ClassVar[str | None] = None
    """Shown on the access point's device in Home Assistant."""
    FIELDS: ClassVar[tuple[DriverField, ...]]
    """Settings this driver needs, in the order they are asked for."""
    SIGNAL_UNIT: ClassVar[str]
    """Scale of AssociatedClient.signal: "%" (0-100) or "dBm" (e.g. -67)."""
    UNIQUE_FIELD: ClassVar[str] = "host"
    """Setting that identifies the AP; two access points can't share its value."""
    POLL_TIMEOUT: ClassVar[int] = 45
    """Seconds allowed for one poll (login plus all commands); must stay well inside
    the integration's 60 s scan interval."""
    OWN_DEVICE: ClassVar[bool] = True
    """Whether the AP gets a Home Assistant device, with client-count and health
    sensors, of its own. False for drivers that read through another integration which
    already registers the AP as a device; that device's area is used instead, found by
    the MAC from device_mac()."""

    REPORTS: ClassVar[frozenset[str]] = frozenset(
        {"name", "location", "model", "firmware", "hardware",
         "uptime_seconds", "cpu_percent", "memory_percent"}
    )
    """AccessPointInfo fields this driver can report. Sensors for the others (e.g. a
    location an AP has no setting for) are not created. Defaults to all of them."""
    EXPERIMENTAL: ClassVar[bool] = False
    """Not yet confirmed on real hardware: shown as experimental in the type list.
    Drivers are separate files, so an experimental one can ship in a release without
    affecting the others; the flag is dropped once a tester confirms it."""
    SOURCE: ClassVar[str | None] = None
    """Name of the Home Assistant data source the driver reads through instead of
    talking to the AP itself (e.g. "unifi": the UniFi Network integration). The
    integration then passes that source as the constructor's second argument and may
    offer UNIQUE_FIELD as a list of what the source knows. None for direct drivers."""

    def device_mac(self) -> str | None:
        """The AP's MAC, for finding the device another integration registered for it."""
        return None

    def __init__(self, config: dict[str, Any]) -> None:
        """Store the settings (keys as declared in FIELDS)."""
        self.config = config

    @abstractmethod
    async def async_get_associated_clients(self) -> list[AssociatedClient]:
        """Return the clients currently associated to this AP.

        Raises AccessPointAuthError for rejected credentials and AccessPointError
        for any other failure.
        """

    async def async_poll(self) -> PollResult:
        """Clients plus whatever the AP reports about itself.

        Drivers that can read device details in the same session override this;
        the default returns clients only.
        """
        return PollResult(await self.async_get_associated_clients())


DRIVERS: dict[str, type[AccessPointDriver]] = {}


def register(cls: type[AccessPointDriver]) -> type[AccessPointDriver]:
    """Class decorator registering a driver under its TYPE."""
    DRIVERS[cls.TYPE] = cls
    return cls


def normalize_mac(mac: str) -> str:
    """Return a MAC as upper-case, colon-separated hex (accepts -, . or no separators)."""
    digits = "".join(ch for ch in mac if ch.isalnum()).upper()
    if len(digits) != 12 or any(ch not in "0123456789ABCDEF" for ch in digits):
        raise ValueError(f"not a MAC address: {mac!r}")
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


# Import the driver modules last so they can use the definitions above and register.
from . import dlink_dap_ssh, openwrt_ssh, unifi_network  # noqa: E402, F401
