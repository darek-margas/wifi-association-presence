"""OpenWrt driver: parsing of hostapd/ubus JSON, in the formats of OpenWrt's hostapd ubus.c.

Run with: python3 -m pytest tests (needs pytest and asyncssh installed).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "custom_components" / "wifi_association_presence"),
)

from ap_drivers import DRIVERS, AccessPointError  # noqa: E402
from ap_drivers.openwrt_ssh import (  # noqa: E402
    MARK,
    OpenWrtSsh,
    band_for_frequency,
    parse_poll,
    split_sections,
)

BOARD = {
    "kernel": "6.6.73",
    "hostname": "Hallway",
    "system": "MediaTek MT7621 ver:1 eco:3",
    "model": "Xiaomi Mi Router 4A Gigabit Edition",
    "board_name": "xiaomi,mi-router-4a-gigabit",
    "release": {"distribution": "OpenWrt", "version": "24.10.0", "description": "OpenWrt 24.10.0 r28427"},
}
INFO = {
    "localtime": 1760000000,
    "uptime": 93784,
    "load": [1824, 3392, 2048],
    "memory": {"total": 125000000, "free": 40000000, "available": 50000000, "buffered": 2000000},
}
STATUS_24 = {"status": "ENABLED", "ssid": "Home", "freq": 2437, "channel": 6, "phy": "phy0"}
STATUS_5 = {"status": "ENABLED", "ssid": "Home 5G", "freq": 5180, "channel": 36, "phy": "phy1"}
CLIENTS_24 = {
    "freq": 2437,
    "clients": {
        "5c:ad:ba:00:00:01": {"auth": True, "assoc": True, "authorized": True, "aid": 1,
                              "bytes": {"rx": 1, "tx": 2}, "signal": -61},
        # still authenticating: not associated yet
        "5c:ad:ba:00:00:02": {"auth": True, "assoc": False, "authorized": False, "aid": 0},
    },
}
CLIENTS_5 = {
    "freq": 5180,
    "clients": {
        # signal as an unsigned u32 (-52)
        "a0:88:b4:00:00:03": {"auth": True, "assoc": True, "authorized": True, "signal": 4294967244},
        # associated, 4-way handshake not finished
        "a0:88:b4:00:00:04": {"auth": True, "assoc": True, "authorized": False, "signal": -70},
        "not-a-mac": {"auth": True, "assoc": True, "authorized": True},
    },
}


def output(*sections: tuple[str, object]) -> str:
    lines = []
    for header, body in sections:
        lines.append(f"{MARK} {header}")
        lines.append(body if isinstance(body, str) else json.dumps(body, indent=1))
    return "\n".join(lines)


FULL = output(
    ("board", BOARD),
    ("info", INFO),
    ("status hostapd.phy0-ap0", STATUS_24),
    ("clients hostapd.phy0-ap0", CLIENTS_24),
    ("status hostapd.phy1-ap0", STATUS_5),
    ("clients hostapd.phy1-ap0", CLIENTS_5),
)


def test_registered_and_experimental() -> None:
    assert DRIVERS["openwrt_ssh"] is OpenWrtSsh
    assert OpenWrtSsh.EXPERIMENTAL
    assert OpenWrtSsh.SIGNAL_UNIT == "dBm"


@pytest.mark.parametrize(
    ("freq", "band"),
    [(2412, "2.4GHz"), (2484, "2.4GHz"), (5180, "5GHz"), (5825, "5GHz"), (5955, "6GHz"),
     (60480, None), (None, None), ("5180", None), (True, None)],
)
def test_band_for_frequency(freq: object, band: str | None) -> None:
    assert band_for_frequency(freq) == band


def test_clients_of_all_radios_only_associated_and_authorized() -> None:
    result = parse_poll(FULL)
    clients = {c.mac: c for c in result.clients}
    assert set(clients) == {"5C:AD:BA:00:00:01", "A0:88:B4:00:00:03"}
    first = clients["5C:AD:BA:00:00:01"]
    assert (first.ssid, first.band, first.signal) == ("Home", "2.4GHz", -61)
    second = clients["A0:88:B4:00:00:03"]
    assert (second.ssid, second.band, second.signal) == ("Home 5G", "5GHz", -52)


def test_device_details() -> None:
    info = parse_poll(FULL).info
    assert info.name == "Hallway"
    assert info.model == "Xiaomi Mi Router 4A Gigabit Edition"
    assert info.firmware == "OpenWrt 24.10.0 r28427"
    assert info.hardware == "xiaomi,mi-router-4a-gigabit"
    assert info.uptime_seconds == 93784
    assert info.memory_percent == 60  # (125 - 50) / 125
    assert info.cpu_percent is None


def test_broken_sections_are_skipped() -> None:
    result = parse_poll(
        output(
            ("board", "Command failed: Not found"),
            ("status hostapd.phy0-ap0", "{ truncated"),
            ("clients hostapd.phy0-ap0", CLIENTS_24),
            ("clients hostapd.phy1-ap0", "[1, 2]"),
        )
    )
    # Clients survive a broken status (no SSID); broken board/info leave the details empty.
    assert [(c.mac, c.ssid) for c in result.clients] == [("5C:AD:BA:00:00:01", None)]
    assert result.info.name is None and result.info.uptime_seconds is None


def test_no_radios_is_no_clients_not_an_error() -> None:
    result = parse_poll(output(("board", BOARD), ("info", INFO)))
    assert result.clients == []
    assert result.info.name == "Hallway"


def test_odd_values_are_dropped() -> None:
    odd = output(
        ("board", {"hostname": "x" * 100, "model": 42, "release": "nope"}),
        ("info", {"uptime": -5, "memory": {"total": 0, "free": 1}}),
        ("clients hostapd.wlan0", {"freq": 2412, "clients": {"5c:ad:ba:00:00:09": {
            "assoc": True, "authorized": True, "signal": 17}}}),
    )
    result = parse_poll(odd)
    assert result.clients[0].signal is None  # not a dBm value
    info = result.info
    assert (info.name, info.model, info.firmware, info.uptime_seconds, info.memory_percent) == (
        None, None, None, None, None)


def test_split_sections_ignores_text_before_first_mark() -> None:
    sections = split_sections(f"banner\n{MARK} board\n{{}}\n{MARK} status hostapd.wlan0\n{{}}")
    assert [(k, o) for k, o, _ in sections] == [("board", None), ("status", "hostapd.wlan0")]


def test_missing_ubus_is_an_error() -> None:
    driver = OpenWrtSsh({"host": "192.0.2.1", "username": "root", "password": "x"})

    async def fake_run(command: str) -> str:
        return "ash: ubus: not found\n"

    driver._run = fake_run  # type: ignore[method-assign]
    with pytest.raises(AccessPointError):
        asyncio.run(driver.async_poll())


def test_poll_uses_one_command() -> None:
    driver = OpenWrtSsh({"host": "192.0.2.1", "username": "root", "password": "x"})
    commands: list[str] = []

    async def fake_run(command: str) -> str:
        commands.append(command)
        return FULL

    driver._run = fake_run  # type: ignore[method-assign]
    result = asyncio.run(driver.async_poll())
    assert len(commands) == 1 and "get_clients" in commands[0]
    assert len(result.clients) == 2


def test_real_openwrt_25_12_5_output() -> None:
    """Output of the driver's own command on OpenWrt 25.12.5 (x86 VM, simulated radios)."""
    fixture = Path(__file__).parent / "fixtures" / "openwrt-25.12.5-x86-hwsim.txt"
    result = parse_poll(fixture.read_text(encoding="utf-8"))
    assert [(c.mac, c.ssid, c.band, c.signal) for c in result.clients] == [
        ("02:00:00:00:01:00", "TestAP", "2.4GHz", -14)
    ]
    assert result.info.firmware == "OpenWrt 25.12.5 r33051-f5dae5ece4"
    assert result.info.name == "OpenWrt"
    assert result.info.uptime_seconds is not None and result.info.memory_percent is not None


def test_every_driver_reports_only_real_info_fields() -> None:
    from dataclasses import fields

    from ap_drivers import AccessPointInfo

    known = {f.name for f in fields(AccessPointInfo)}
    for type_, cls in DRIVERS.items():
        assert cls.REPORTS <= known, (type_, cls.REPORTS - known)
    assert "location" not in DRIVERS["openwrt_ssh"].REPORTS
    assert "cpu_percent" not in DRIVERS["openwrt_ssh"].REPORTS
    assert "location" in DRIVERS["dlink_dap_ssh"].REPORTS
