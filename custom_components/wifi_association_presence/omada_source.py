"""Controller data for TP-Link Omada access points, from Home Assistant's TP-Link Omada
integration.

That integration polls its client list only every 5 minutes, too slow for presence, so
the controller's connected clients and its devices are requested here instead, over the
integration's own logged-in client: once per controller per poll round, shared by all
its access points (as for UniFi). Its device list, refreshed by the integration, is only
used to offer the access points in the setup form.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import SelectOptionDict

from wifi_ap_associations import AccessPointError, normalize_mac
from wifi_ap_associations.omada_network import DEVICE_STATES, STATE_CONNECTED, is_access_point
from .const import DOMAIN

OMADA_DOMAIN = "tplink_omada"
# All access points are polled in the same round, within seconds of each other; one
# read serves them all. Well under SCAN_INTERVAL, so every round fetches anew.
CONTROLLER_TTL = 20
DATA_CONTROLLER_CACHE = f"{DOMAIN}_omada_controller"


@dataclass(slots=True)
class _ControllerCache:
    """The last devices and connected clients of one controller, or why they couldn't
    be read (remembered for the TTL too, so a round makes one attempt, not one per AP)."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    fetched: float | None = None
    devices: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    clients: list[Mapping[str, Any]] = field(default_factory=list)
    error: AccessPointError | None = None


@dataclass(frozen=True, slots=True)
class _OmadaSite:
    """A loaded TP-Link Omada config entry (one controller site) and its controller."""

    entry_id: str
    title: str
    controller: Any


def _sites(hass: HomeAssistant) -> Iterator[_OmadaSite]:
    """The loaded TP-Link Omada integrations."""
    for entry in hass.config_entries.async_loaded_entries(OMADA_DOMAIN):
        controller = getattr(entry, "runtime_data", None)
        if getattr(controller, "omada_client", None) is not None:
            yield _OmadaSite(entry.entry_id, entry.title, controller)


def _mac(value: Any) -> str | None:
    try:
        return normalize_mac(value)
    except (TypeError, ValueError):
        return None


def _known_devices(site: _OmadaSite) -> Iterator[Mapping[str, Any]]:
    """Raw device records from the integration's own (5-minute) device list."""
    coordinator = getattr(site.controller, "devices_coordinator", None)
    for device in list((getattr(coordinator, "data", None) or {}).values()):
        if isinstance(raw := getattr(device, "raw_data", None), Mapping):
            yield raw


def _access_points(hass: HomeAssistant) -> Iterator[tuple[_OmadaSite, str, Mapping[str, Any]]]:
    """Every access point the loaded Omada integrations know, with its normalized MAC."""
    for site in _sites(hass):
        for raw in _known_devices(site):
            if is_access_point(raw) and (mac := _mac(raw.get("mac"))):
                yield site, mac, raw


def access_point_options(hass: HomeAssistant, exclude: set[str]) -> list[SelectOptionDict]:
    """Dropdown options for choosing an Omada access point, by MAC."""
    multiple = len(list(_sites(hass))) > 1
    options: list[SelectOptionDict] = []
    for site, mac, raw in _access_points(hass):
        if mac in exclude:
            continue
        details = [raw.get("model") or "?", mac]
        state = raw.get("statusCategory")
        if state != STATE_CONNECTED:
            details.append(str(DEVICE_STATES.get(state, state)))
        if multiple:
            details.append(site.title)
        options.append(
            SelectOptionDict(value=mac, label=f"{raw.get('name') or mac}  ({', '.join(details)})")
        )
    return sorted(options, key=lambda option: option["label"].casefold())


class HassOmadaSource:
    """OmadaSource backed by Home Assistant's TP-Link Omada integration."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Read from the Omada integrations loaded in this Home Assistant."""
        self.hass = hass

    def _site_of(self, mac: str) -> _OmadaSite | None:
        for site, ap_mac, _raw in _access_points(self.hass):
            if ap_mac == mac:
                return site
        return None

    async def _read(self, mac: str) -> _ControllerCache:
        site = self._site_of(mac)
        if site is None:
            raise AccessPointError(f"{mac} is not managed by a loaded TP-Link Omada integration")
        caches: dict[str, _ControllerCache] = self.hass.data.setdefault(DATA_CONTROLLER_CACHE, {})
        cache = caches.setdefault(site.entry_id, _ControllerCache())
        async with cache.lock:
            if cache.fetched is None or time.monotonic() - cache.fetched > CONTROLLER_TTL:
                cache.fetched = time.monotonic()
                try:
                    cache.devices, cache.clients = await _async_request(site)
                except AccessPointError as err:
                    cache.error = err
                    raise
                cache.error = None
            if cache.error is not None:
                raise cache.error
            return cache

    async def async_get_device(self, mac: str) -> Mapping[str, Any] | None:
        """The access point's device record, as the controller reports it now."""
        return (await self._read(mac)).devices.get(mac)

    async def async_get_clients(self, mac: str) -> list[Mapping[str, Any]]:
        """The connected clients of the controller site managing this access point."""
        return (await self._read(mac)).clients


async def _async_request(
    site: _OmadaSite,
) -> tuple[dict[str, Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Ask the controller for its devices and its connected clients."""
    from aiohttp import ClientError

    errors: tuple[type[BaseException], ...] = (ClientError, OSError)
    try:  # installed with the Omada integration, which is loaded by now
        from tplink_omada_client.exceptions import OmadaClientException

        errors = (OmadaClientException, *errors)
    except ImportError:
        pass

    client = site.controller.omada_client
    try:
        devices = {
            mac: raw
            for device in await client.get_devices()
            if isinstance(raw := getattr(device, "raw_data", None), Mapping)
            and (mac := _mac(raw.get("mac")))
        }
        clients = [
            raw
            async for connected in client.get_connected_clients()
            if isinstance(raw := getattr(connected, "raw_data", None), Mapping)
        ]
    except errors as err:
        raise AccessPointError(
            f"TP-Link Omada controller {site.title} did not return its clients: {err!r}"
        ) from err
    return devices, clients
