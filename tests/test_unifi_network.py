"""Tests for the UniFi driver, with records shaped like a UniFi Network controller's.

Run with: python3 -m pytest tests (needs pytest and asyncssh installed).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "custom_components" / "wifi_association_presence"),
)

from ap_drivers import DRIVERS, AccessPointError, AssociatedClient  # noqa: E402
from ap_drivers.unifi_network import (  # noqa: E402
    UnifiNetworkDriver,
    is_access_point,
    parse_clients,
    parse_device,
)

AP = "78:8A:20:00:00:10"
OTHER_AP = "78:8A:20:00:00:20"

DEVICE = {
    "mac": "78:8a:20:00:00:10",
    "type": "uap",
    "model": "U7PG2",
    "name": "Hallway AP",
    "version": "6.6.77.15402",
    "state": 1,
    "uptime": 86400,
    "system-stats": {"cpu": "7.3", "mem": "44.9", "uptime": "86400"},
}

CLIENTS = [
    {
        "mac": "5c:ad:ba:00:00:01",
        "is_wired": False,
        "ap_mac": "78:8a:20:00:00:10",
        "essid": "Home",
        "radio": "na",
        "signal": -58,
        "uptime": 5321,
        "_uptime_by_uap": 5300,
    },
    {
        "mac": "4c:fc:aa:00:00:02",
        "is_wired": False,
        "ap_mac": "78:8a:20:00:00:10",
        "essid": "Home IoT",
        "radio": "ng",
        "signal": -71,
        "uptime": 120,
    },
    # Associated to another access point.
    {"mac": "4c:fc:aa:00:00:03", "is_wired": False, "ap_mac": "78:8a:20:00:00:20",
     "essid": "Home", "radio": "6e", "signal": -50},
    # Wired, behind a switch; never a Wi-Fi association.
    {"mac": "00:11:32:00:00:04", "is_wired": True, "sw_mac": "78:8a:20:00:00:30"},
    # Malformed records are skipped.
    {"is_wired": False, "ap_mac": "78:8a:20:00:00:10"},
    {"mac": "nonsense", "is_wired": False, "ap_mac": "78:8a:20:00:00:10"},
]


class FakeSource:
    """A controller with one access point."""

    def __init__(self, device=DEVICE, clients=CLIENTS) -> None:
        self.device = device
        self.clients = clients

    async def async_get_device(self, mac):
        return self.device if self.device and mac == AP else None

    async def async_get_clients(self, mac):
        return self.clients


def test_registered() -> None:
    assert DRIVERS["unifi_network"] is UnifiNetworkDriver
    assert UnifiNetworkDriver.UNIQUE_FIELD == "ap_mac"
    assert UnifiNetworkDriver.POLL_TIMEOUT < DRIVERS["dlink_dap_ssh"].POLL_TIMEOUT


def test_invalid_mac_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        UnifiNetworkDriver({"ap_mac": "not-a-mac"}, FakeSource())


def test_parse_clients() -> None:
    assert parse_clients(CLIENTS, AP) == [
        AssociatedClient("5C:AD:BA:00:00:01", "Home", "5GHz", -58, 5300),
        AssociatedClient("4C:FC:AA:00:00:02", "Home IoT", "2.4GHz", -71, 120),
    ]
    assert parse_clients(CLIENTS, OTHER_AP) == [
        AssociatedClient("4C:FC:AA:00:00:03", "Home", "6GHz", -50, None),
    ]


def test_parse_clients_positive_or_missing_signal() -> None:
    raw = [{"mac": "5c:ad:ba:00:00:01", "ap_mac": AP, "signal": 63, "radio": "xx"},
           {"mac": "5c:ad:ba:00:00:02", "ap_mac": AP},
           {"mac": "5c:ad:ba:00:00:03", "ap_mac": AP, "signal": 0}]
    first, second, third = parse_clients(raw, AP)
    assert (first.signal, first.band, first.ssid) == (-63, None, None)
    assert second.signal is None
    assert third.signal == 0  # zero is a value, not "unknown"


def test_parse_device() -> None:
    info = parse_device(DEVICE)
    assert info.name == "Hallway AP"
    assert info.model == "U7PG2"
    assert info.firmware == "6.6.77.15402"
    assert info.uptime_seconds == 86400
    assert (info.cpu_percent, info.memory_percent) == (7, 45)
    assert parse_device({"mac": AP}).name is None


def test_is_access_point() -> None:
    assert is_access_point(DEVICE)
    assert is_access_point({"type": "udm", "radio_table": [{"radio": "na"}]})
    assert not is_access_point({"type": "usw"})
    assert not is_access_point({"type": "ugw", "radio_table": []})


def test_poll() -> None:
    driver = UnifiNetworkDriver({"ap_mac": "78-8a-20-00-00-10"}, FakeSource())
    result = asyncio.run(driver.async_poll())
    assert [c.mac for c in result.clients] == ["5C:AD:BA:00:00:01", "4C:FC:AA:00:00:02"]
    assert result.info.name == "Hallway AP"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (None, "inside Home Assistant"),
        (FakeSource(device=None), "not an access point"),
        (FakeSource(device={**DEVICE, "state": 0}), "disconnected"),
        (FakeSource(device={**DEVICE, "state": 99}), "99"),
    ],
)
def test_poll_unavailable(source, message) -> None:
    driver = UnifiNetworkDriver({"ap_mac": AP}, source)
    with pytest.raises(AccessPointError, match=message):
        asyncio.run(driver.async_poll())
