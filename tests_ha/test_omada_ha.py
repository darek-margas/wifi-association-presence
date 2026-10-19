"""The TP-Link Omada driver in Home Assistant, against a fake TP-Link Omada integration."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "wifi_association_presence"
AP = "C0:C9:E3:4B:AF:0E"
DEVICES = [
    {"mac": "C0-C9-E3-4B-AF-0E", "type": "ap", "name": "Spare Room", "model": "EAP653",
     "firmwareVersion": "1.0.12", "uptimeLong": 3600, "cpuUtil": 5, "memUtil": 40,
     "statusCategory": 1},
    {"mac": "C0-C9-E3-4B-A7-FE", "type": "ap", "name": "Living Room", "model": "EAP653",
     "statusCategory": 0},
    {"mac": "54-AF-97-00-00-01", "type": "switch", "name": "Switch", "statusCategory": 1},
]
CLIENTS = [
    {"mac": "2E-DC-E1-C4-37-D3", "wireless": True, "active": True, "ssid": "Home",
     "apMac": "C0-C9-E3-4B-AF-0E", "radioId": 1, "rssi": -63, "uptime": 2091},
]


class FakeOmadaClient:
    def __init__(self):
        self.reads = 0

    async def get_devices(self):
        self.reads += 1
        return [SimpleNamespace(raw_data=dict(d)) for d in DEVICES]

    async def get_connected_clients(self):
        for c in CLIENTS:
            yield SimpleNamespace(raw_data=dict(c))


@pytest.fixture
def omada(hass):
    entry = MockConfigEntry(domain="tplink_omada", title="Home site", state=ConfigEntryState.LOADED)
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        omada_client=FakeOmadaClient(),
        devices_coordinator=SimpleNamespace(
            data={d["mac"]: SimpleNamespace(raw_data=dict(d)) for d in DEVICES}
        ),
    )
    return entry


def ap_sub(mac: str, title: str) -> ConfigSubentryData:
    return ConfigSubentryData(subentry_type="access_point", title=title, unique_id=None,
                              data={"driver": "omada_network", "ap_mac": mac, "name": ""})


async def test_setup_and_presence(hass, omada):
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=[
        ap_sub(AP, "Spare Room"),
        ConfigSubentryData(subentry_type="tracked_device", title="Phone",
                           unique_id="2E:DC:E1:C4:37:D3", data={"mac": "2E:DC:E1:C4:37:D3"}),
    ])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    trackers = [s for s in hass.states.async_all() if s.entity_id.startswith("device_tracker.")]
    assert trackers and trackers[0].state == "home"
    assert omada.runtime_data.omada_client.reads == 1


async def test_disconnected_ap_is_unavailable_not_fatal(hass, omada):
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=[
        ap_sub(AP, "Spare Room"), ap_sub("C0:C9:E3:4B:A7:FE", "Living Room"),
    ])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    # one controller read shared by both APs
    assert omada.runtime_data.omada_client.reads == 1


async def test_subentry_flow_lists_omada_access_points(hass, omada):
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "access_point"), context={"source": "user"})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"driver": "omada_network"})
    assert result["step_id"] == "details"
    sel = next(v for k, v in result["data_schema"].schema.items() if str(k) == "ap_mac")
    assert sorted(o["value"] for o in sel.config["options"]) == ["C0:C9:E3:4B:A7:FE", AP]
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"ap_mac": AP})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["title"] == "Spare Room"


async def test_no_omada(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "access_point"), context={"source": "user"})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"driver": "omada_network"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_omada_access_points"
