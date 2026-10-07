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
    rssi: int | None = None
    """Signal strength on the AP's own scale (percent on D-Link DAP)."""
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
from . import dlink_dap_ssh  # noqa: E402, F401
