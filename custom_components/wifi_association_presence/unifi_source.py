"""Controller data for UniFi access points, from Home Assistant's UniFi Network integration.

The UniFi integration keeps its devices current over the controller's websocket, so
access point details are read from memory. Its client list, however, keeps clients
after they disconnect, so the controller's active client list is requested instead:
once per controller per poll round, shared by all its access points.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import SelectOptionDict

from .ap_drivers import AccessPointError, normalize_mac
from .ap_drivers.unifi_network import DEVICE_STATES, STATE_CONNECTED, is_access_point
from .const import DOMAIN

UNIFI_DOMAIN = "unifi"
# All access points are polled in the same round, within seconds of each other; one
# request serves them all. Well under SCAN_INTERVAL, so every round fetches anew.
CLIENTS_TTL = 20
DATA_CLIENT_CACHE = f"{DOMAIN}_unifi_clients"


@dataclass(slots=True)
class _ClientCache:
    """The last active client list of one controller, or why it couldn't be read.

    A failure is remembered for the TTL as well, so one poll round makes one attempt
    at a controller that is down rather than one per access point.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    fetched: float | None = None
    clients: list[Mapping[str, Any]] = field(default_factory=list)
    error: AccessPointError | None = None


@dataclass(frozen=True, slots=True)
class _UnifiHub:
    """A loaded UniFi Network config entry and its hub (holding the aiounifi API)."""

    entry_id: str
    title: str
    hub: Any


def _hubs(hass: HomeAssistant) -> Iterator[_UnifiHub]:
    """The loaded UniFi Network integrations."""
    for entry in hass.config_entries.async_loaded_entries(UNIFI_DOMAIN):
        hub = getattr(entry, "runtime_data", None)
        if getattr(hub, "api", None) is not None:
            yield _UnifiHub(entry.entry_id, entry.title, hub)


def _mac(value: Any) -> str | None:
    try:
        return normalize_mac(value)
    except (TypeError, ValueError):
        return None


def _access_points(hass: HomeAssistant) -> Iterator[tuple[_UnifiHub, str, Mapping[str, Any]]]:
    """Every access point of every loaded UniFi integration, with its normalized MAC."""
    for unifi in _hubs(hass):
        for device in list(unifi.hub.api.devices.values()):
            raw = device.raw
            if is_access_point(raw) and (mac := _mac(raw.get("mac"))):
                yield unifi, mac, raw


def access_point_options(hass: HomeAssistant, exclude: set[str]) -> list[SelectOptionDict]:
    """Dropdown options for choosing a UniFi access point, by MAC."""
    multiple = len(list(_hubs(hass))) > 1
    options: list[SelectOptionDict] = []
    for unifi, mac, raw in _access_points(hass):
        if mac in exclude:
            continue
        details = [raw.get("model") or "?", mac]
        if raw.get("state") != STATE_CONNECTED:
            details.append(str(DEVICE_STATES.get(raw.get("state"), raw.get("state"))))
        if multiple:
            details.append(unifi.title)
        options.append(
            SelectOptionDict(
                value=mac, label=f"{raw.get('name') or mac}  ({', '.join(details)})"
            )
        )
    return sorted(options, key=lambda option: option["label"].casefold())


class HassUnifiSource:
    """UnifiSource backed by Home Assistant's UniFi Network integration."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Read from the UniFi integrations loaded in this Home Assistant."""
        self.hass = hass

    def _find(self, mac: str) -> tuple[_UnifiHub, Mapping[str, Any]] | None:
        for unifi, ap_mac, raw in _access_points(self.hass):
            if ap_mac == mac:
                return unifi, raw
        return None

    async def async_get_device(self, mac: str) -> Mapping[str, Any] | None:
        """The access point's device record, as last pushed by the controller."""
        found = self._find(mac)
        return found[1] if found else None

    async def async_get_clients(self, mac: str) -> list[Mapping[str, Any]]:
        """The active clients of the controller managing this access point."""
        found = self._find(mac)
        if found is None:
            raise AccessPointError(f"{mac} is not managed by a loaded UniFi integration")
        unifi = found[0]
        caches: dict[str, _ClientCache] = self.hass.data.setdefault(DATA_CLIENT_CACHE, {})
        cache = caches.setdefault(unifi.entry_id, _ClientCache())
        async with cache.lock:
            if cache.fetched is None or time.monotonic() - cache.fetched > CLIENTS_TTL:
                cache.fetched = time.monotonic()
                try:
                    cache.clients = await _async_request_active_clients(unifi)
                except AccessPointError as err:
                    cache.error = err
                    raise
                cache.error = None
            if cache.error is not None:
                raise cache.error
            return cache.clients


async def _async_request_active_clients(unifi: _UnifiHub) -> list[Mapping[str, Any]]:
    """Ask the controller for its currently connected clients (stat/sta)."""
    # aiounifi is installed with the UniFi integration, which is loaded by now.
    from aiohttp import ClientError

    try:
        from aiounifi.errors import AiounifiException
        from aiounifi.models.client import ClientListRequest
    except ImportError as err:
        raise AccessPointError(
            "the aiounifi library of the UniFi Network integration is not available"
        ) from err

    try:
        response = await unifi.hub.api.request(ClientListRequest.create())
    except (AiounifiException, ClientError, OSError) as err:
        raise AccessPointError(
            f"UniFi controller {unifi.title} did not return its clients: {err!r}"
        ) from err
    return list(response.get("data") or [])
