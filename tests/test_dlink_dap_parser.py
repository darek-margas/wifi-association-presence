"""Parser tests for the D-Link DAP driver, using real DAP-2610 console output.

Run with: python3 -m pytest tests (needs pytest and asyncssh installed).
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "custom_components" / "wifi_association_presence"),
)

from ap_drivers import normalize_mac  # noqa: E402
from ap_drivers.dlink_dap_ssh import (  # noqa: E402
    parse_cli_value,
    parse_hardware,
    parse_uptime,
    parse_clientinfo,
    ssid_name_command,
)

FIVE_GHZ = """\
config wlan 1
current band is: 1 (0:2.4G, 1:5G)
WAP-> get clientinfo
Client1--time:1430
Client1--ssid: primary SSID
Client1--mac:76:F4:C1:CB:89:12
Client1--auth:WPA2-EAP
Client1--rssi:98
Client1--mode:11ac
Client1--psmode:0
Client1--rx_bytes:1124006
Client1--tx_bytes:1757062
---------------------------------------------------------------------
Client2--time:1455
Client2--ssid: primary SSID
Client2--mac:5C:AD:BA:0A:34:DC
Client2--auth:WPA2-EAP
Client2--rssi:100
Client2--mode:11ac
Client2--psmode:1
Client2--rx_bytes:757832
Client2--tx_bytes:8043007
---------------------------------------------------------------------
Client1--time:1180
Client1--ssid: MULTI-SSID index 3
Client1--mac:00:F6:20:70:7D:33
Client1--auth:WPA2-PSK
Client1--rssi:100
Client1--mode:11ac
Client1--psmode:0
Client1--rx_bytes:1094735
Client1--tx_bytes:2150123
---------------------------------------------------------------------
WAP-> """


def test_parses_all_clients_with_restarting_numbering() -> None:
    clients = parse_clientinfo(FIVE_GHZ, "5GHz")
    assert [c.mac for c in clients] == [
        "76:F4:C1:CB:89:12",
        "5C:AD:BA:0A:34:DC",
        "00:F6:20:70:7D:33",
    ]
    first = clients[0]
    assert first.ssid == "primary SSID"
    assert first.band == "5GHz"
    assert first.rssi == 98
    assert first.connected_seconds == 1430
    assert clients[2].ssid == "MULTI-SSID index 3"


def test_empty_output() -> None:
    assert parse_clientinfo("get clientinfo\nWAP-> ") == []


def test_record_without_separator_is_split() -> None:
    text = "Client1--mac:AA:BB:CC:DD:EE:01\nClient2--mac:aa-bb-cc-dd-ee-02\n"
    assert [c.mac for c in parse_clientinfo(text)] == [
        "AA:BB:CC:DD:EE:01",
        "AA:BB:CC:DD:EE:02",
    ]


def test_normalize_mac() -> None:
    assert normalize_mac("aabb.ccdd.eeff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "AA:BB:CC:DD:EE:FF"


def test_ssid_name_command() -> None:
    assert ssid_name_command("primary SSID") == "get ssid"
    assert ssid_name_command("MULTI-SSID index 3") == "get multi-ssid 3"
    assert ssid_name_command("Kids") is None


def test_parse_cli_value() -> None:
    # Real DAP-3662 replies.
    assert parse_cli_value("get ssid\nSSID:Power\nWAP->\n", "get ssid") == "Power"
    assert (
        parse_cli_value(
            "get multi-ssid 3\nSSID of Multi-SSID (index 3) is Internal\nWAP->\n",
            "get multi-ssid 3",
        )
        == "Internal"
    )
    assert parse_cli_value("get ssid\nSSID : Primary\nWAP-> ", "get ssid") == "Primary"
    assert (
        parse_cli_value("get multi-ssid 9\nInvalid parameter: 9\nWAP-> ", "get multi-ssid 9")
        is None
    )


def test_device_details() -> None:
    # Real DAP-2610 replies.
    assert parse_cli_value("version\n\n SOFTWARE_VERSION: v2.06\nWAP->\n", "version") == "v2.06"
    assert parse_cli_value("get systemname\nStudio\nWAP->\n", "get systemname") == "Studio"
    assert parse_cli_value("get location\nLevel1\nWAP->\n", "get location") == "Level1"
    assert parse_cli_value("get cpuinfo\ncpuinfo:15\nWAP->\n", "get cpuinfo") == "15"
    assert parse_hardware("get hardware\nrev A1G\nWAP->\n") == "A1G"
    assert parse_uptime("get uptime\nAP Uptime -- Day 62,  0:41:46\nWAP->\n") == (
        62 * 86400 + 41 * 60 + 46
    )
    assert parse_uptime("get uptime\nWAP->\n") is None
