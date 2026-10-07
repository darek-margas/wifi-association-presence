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
from ap_drivers.dlink_dap_ssh import parse_clientinfo  # noqa: E402

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
