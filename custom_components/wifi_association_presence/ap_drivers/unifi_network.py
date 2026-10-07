"""UniFi access points, read through Home Assistant's UniFi Network integration.

Instead of logging in to the access point, this driver asks the UniFi Network
controller (UniFi OS console, Cloud Key or self-hosted Network application) which
wireless clients are associated to one of its access points. It reuses the
connection Home Assistant's own UniFi Network integration already holds, so no
credentials are entered here: the integration only has to be set up.

The controller's active client list (stat/sta) holds only currently connected
clients, one record per client:

    {"mac": "5c:ad:ba:00:00:01", "is_wired": false, "ap_mac": "78:8a:20:00:00:10",
     "essid": "Home", "radio": "na", "signal": -58, "uptime": 5321, ...}

and each access point is a device record ("type": "uap", or a console or gateway
with built-in radios), with "state" 1 while it is connected to the controller.

The source of that data is injected (see UnifiSource), so this module keeps the
package free of Home Assistant imports and can be tested on its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from . import (
    AccessPointDriver,
    AccessPointError,
    AccessPointInfo,
    AssociatedClient,
    DriverField,
    PollResult,
    normalize_mac,
    register,
)

CONF_AP_MAC = "ap_mac"

# UniFi radio codes: ng = 2.4 GHz, na = 5 GHz, 6e = 6 GHz, ad = 60 GHz.
RADIO_BANDS = {"ng": "2.4GHz", "na": "5GHz", "6e": "6GHz", "ad": "60GHz"}

DEVICE_STATES = {
    0: "disconnected",
    1: "connected",
    2: "pending adoption",
    4: "upgrading",
    5: "provisioning",
    6: "heartbeat missed",
    7: "adopting",
    9: "adoption failed",
    10: "isolated",
    11: "adoption failed",
}
STATE_CONNECTED = 1


class UnifiSource(Protocol):
    """Where the driver gets controller data (Home Assistant's UniFi integration)."""

    async def async_get_device(self, mac: str) -> Mapping[str, Any] | None:
        """The raw device record of the access point with this MAC, if known."""

    async def async_get_clients(self, mac: str) -> list[Mapping[str, Any]]:
        """Raw records of the clients currently connected to the controller that
        manages the access point with this MAC."""


def is_access_point(device: Mapping[str, Any]) -> bool:
    """Whether a UniFi device record has Wi-Fi radios (an AP, or a console with radios)."""
    return device.get("type") == "uap" or bool(device.get("radio_table"))


def _int(value: Any) -> int | None:
    """An int from a number or numeric text; None for anything else."""
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def parse_clients(
    raw_clients: list[Mapping[str, Any]], ap_mac: str
) -> list[AssociatedClient]:
    """The wireless clients associated to the access point with this MAC."""
    clients: list[AssociatedClient] = []
    for raw in raw_clients:
        if raw.get("is_wired", False):
            continue
        try:
            if normalize_mac(raw.get("ap_mac") or "") != ap_mac:
                continue
            mac = normalize_mac(raw["mac"])
        except (KeyError, ValueError):
            continue
        signal = _int(raw.get("signal"))
        clients.append(
            AssociatedClient(
                mac=mac,
                ssid=raw.get("essid") or None,
                band=RADIO_BANDS.get(raw.get("radio") or ""),
                # Some controllers report signal as a positive number; dBm is negative.
                signal=-abs(signal) if signal is not None else None,
                connected_seconds=_int(raw.get("_uptime_by_uap", raw.get("uptime"))),
            )
        )
    return clients


def parse_device(device: Mapping[str, Any]) -> AccessPointInfo:
    """What the controller reports about the access point."""
    stats = device.get("system-stats") or {}
    return AccessPointInfo(
        name=device.get("name") or None,
        model=device.get("model") or None,
        firmware=device.get("version") or None,
        uptime_seconds=_int(device.get("uptime")),
        cpu_percent=_int(stats.get("cpu")),
        memory_percent=_int(stats.get("mem")),
    )


@register
class UnifiNetworkDriver(AccessPointDriver):
    """One UniFi access point, read through the UniFi Network integration."""

    TYPE = "unifi_network"
    NAME = "UniFi (via the UniFi Network integration)"
    # As the UniFi integration names it, so sharing its device changes nothing there.
    MANUFACTURER = "Ubiquiti Networks"
    FIELDS = (DriverField(CONF_AP_MAC),)
    SIGNAL_UNIT = "dBm"
    UNIQUE_FIELD = CONF_AP_MAC
    # Reads through Home Assistant's UniFi Network integration (see the integration's
    # sources.py), which also lists the access points to pick from.
    SOURCE = "unifi"
    # One HTTPS request to a local controller, not an SSH console session.
    POLL_TIMEOUT = 20
    # The UniFi integration already registers the AP as a device, with client count,
    # uptime, CPU and memory; its area is what the tracked devices report.
    OWN_DEVICE = False

    def device_mac(self) -> str | None:
        """The MAC of the device the UniFi integration registered for this AP."""
        return self.ap_mac

    def __init__(self, config: dict[str, Any], source: UnifiSource | None = None) -> None:
        """Store the settings and the controller data source."""
        super().__init__(config)
        self.source = source
        self.ap_mac = normalize_mac(config[CONF_AP_MAC])

    async def async_get_associated_clients(self) -> list[AssociatedClient]:
        """Clients associated to this access point."""
        return (await self.async_poll()).clients

    async def async_poll(self) -> PollResult:
        """Clients and device details, as the controller reports them."""
        if self.source is None:
            raise AccessPointError("UniFi access points can only be read inside Home Assistant")
        device = await self.source.async_get_device(self.ap_mac)
        if device is None:
            raise AccessPointError(
                f"{self.ap_mac} is not an access point of any loaded UniFi Network integration"
            )
        state = device.get("state")
        if state != STATE_CONNECTED:
            raise AccessPointError(
                f"the controller reports the access point as {DEVICE_STATES.get(state, state)}"
            )
        raw_clients = await self.source.async_get_clients(self.ap_mac)
        return PollResult(parse_clients(raw_clients, self.ap_mac), parse_device(device))
