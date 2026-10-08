"""The UniFi driver in Home Assistant, against a fake UniFi Network hub."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch
from types import SimpleNamespace

from freezegun.api import FrozenDateTimeFactory
import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "wifi_association_presence"
AP = "78:8A:20:00:00:10"
DEVICES = {
    "78:8a:20:00:00:10": {"mac": "78:8a:20:00:00:10", "type": "uap", "model": "U7PG2",
        "name": "Hallway AP", "version": "6.6.77", "state": 1, "uptime": 3600,
        "system-stats": {"cpu": "5", "mem": "40"}},
    "78:8a:20:00:00:20": dict(**{"mac": "78:8a:20:00:00:20", "type": "uap", "model": "U6LR",
        "name": "Garden AP", "version": "6.6.77", "state": 0}),
    "78:8a:20:00:00:30": {"mac": "78:8a:20:00:00:30", "type": "usw", "name": "Switch", "state": 1},
}
CLIENTS = [
    {"mac": "5c:ad:ba:00:00:01", "is_wired": False, "ap_mac": "78:8a:20:00:00:10",
     "essid": "Home", "radio": "na", "signal": -58, "uptime": 100},
]


class FakeApi:
    def __init__(self):
        self.devices = {k: SimpleNamespace(raw=dict(v)) for k, v in DEVICES.items()}
        self.requests = 0
    async def request(self, req):
        assert req.path == "/stat/sta"
        self.requests += 1
        return {"meta": {"rc": "ok"}, "data": CLIENTS}


@pytest.fixture
def unifi(hass):
    entry = MockConfigEntry(domain="unifi", title="UDM", state=ConfigEntryState.LOADED)
    entry.add_to_hass(hass)
    async def reset(): return True
    entry.runtime_data = SimpleNamespace(api=FakeApi(), async_reset=reset)
    return entry


async def test_setup_and_presence(hass, unifi):
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=[
        ConfigSubentryData(subentry_type="access_point", title="Hallway AP", unique_id=None,
            data={"driver": "unifi_network", "ap_mac": AP, "name": ""}),
        ConfigSubentryData(subentry_type="access_point", title="Garden AP", unique_id=None,
            data={"driver": "unifi_network", "ap_mac": "78:8A:20:00:00:20", "name": ""}),
        ConfigSubentryData(subentry_type="tracked_device", title="Phone",
            unique_id="5C:AD:BA:00:00:01", data={"mac": "5C:AD:BA:00:00:01"}),
    ])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    states = {s.entity_id: (s.state, dict(s.attributes)) for s in hass.states.async_all()}
    for k, v in sorted(states.items()):
        print(k, v)
    trackers = [v for k, v in states.items() if k.startswith("device_tracker.")]
    assert trackers and trackers[0][0] == "home"
    # one request shared by both APs
    assert unifi.runtime_data.api.requests == 1
    assert any(v[0] == "Hallway AP" for k, v in states.items() if k.startswith("sensor.") and "access_point" in k)


async def test_subentry_flow(hass, unifi):
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=[
        ConfigSubentryData(subentry_type="access_point", title="Garden AP", unique_id=None,
            data={"driver": "unifi_network", "ap_mac": "78:8A:20:00:00:20", "name": ""}),
    ])
    entry.add_to_hass(hass)
    unifi.runtime_data.api.devices["78:8a:20:00:00:20"].raw["state"] = 1
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    unifi.runtime_data.api.devices["78:8a:20:00:00:20"].raw["state"] = 0
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "access_point"), context={"source": "user"})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"driver": "unifi_network"})
    assert result["step_id"] == "details"
    schema = result["data_schema"].schema
    sel = next(v for k, v in schema.items() if str(k) == "ap_mac")
    opts = sel.config["options"]
    print(opts)
    assert [o["value"] for o in opts] == [AP]  # switch and already-added Garden excluded
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"ap_mac": AP})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["title"] == "Hallway AP"
    await hass.async_block_till_done()
    sub = [s for s in entry.subentries.values() if s.data.get("ap_mac") == AP][0]
    # reconfigure keeps its own AP selectable
    result = await entry.start_subentry_reconfigure_flow(hass, sub.subentry_id)
    sel = next(v for k, v in result["data_schema"].schema.items() if str(k) == "ap_mac")
    assert AP in [o["value"] for o in sel.config["options"]]


async def test_no_unifi(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "access_point"), context={"source": "user"})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"driver": "unifi_network"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_unifi_access_points"


async def test_debug(hass):
    import custom_components
    from homeassistant.loader import async_get_custom_components, async_get_integration
    print("PATH", custom_components.__path__)
    print("COMPS", await async_get_custom_components(hass))
    try:
        await async_get_integration(hass, DOMAIN)
    except Exception as e:
        print("ERR", repr(e))


async def _setup(hass, unifi, extra=()):
    unifi.runtime_data.api.devices["78:8a:20:00:00:20"].raw["state"] = 1
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=[
        ConfigSubentryData(subentry_type="access_point", title="Hallway AP", unique_id=None,
            data={"driver": "unifi_network", "ap_mac": AP, "name": ""}),
        ConfigSubentryData(subentry_type="access_point", title="Garden AP", unique_id=None,
            data={"driver": "unifi_network", "ap_mac": "78:8A:20:00:00:20", "name": ""}),
        ConfigSubentryData(subentry_type="tracked_device", title="Phone",
            unique_id="5C:AD:BA:00:00:01", data={"mac": "5C:AD:BA:00:00:01"}),
        *extra,
    ])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_total_failure_is_tolerated_within_grace(
    hass, unifi, freezer: FrozenDateTimeFactory
):
    entry = await _setup(hass, unifi)
    assert hass.states.get("device_tracker.phone").state == "home"
    api = unifi.runtime_data.api

    from aiounifi.errors import RequestError
    attempts = 0
    async def boom(req):
        nonlocal attempts
        attempts += 1
        raise RequestError("controller rebooting")
    api.request = boom
    coordinator = entry.runtime_data
    def expire():
        hass.data["wifi_association_presence_unifi_clients"].clear()
    # Default grace period (180 s) after the last successful poll.
    for n in range(1, 3):  # 61 s and 122 s: still home, AP sensors unavailable
        expire()
        freezer.tick(timedelta(seconds=61))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.last_update_success, n
        assert hass.states.get("device_tracker.phone").state == "home", n
        assert not any(s.available for s in coordinator.data.access_points.values()), n
    expire()
    freezer.tick(timedelta(seconds=61))
    await coordinator.async_refresh()  # 183 s: past the grace period, give up
    await hass.async_block_till_done()
    assert not coordinator.last_update_success
    assert attempts == 3  # one request per round, not one per AP
    assert hass.states.get("device_tracker.phone").state == "unavailable"
    # recovery
    async def ok(req):
        return {"meta": {"rc": "ok"}, "data": CLIENTS}
    api.request = ok
    expire()
    freezer.tick(timedelta(seconds=61))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.phone").state == "home"
    assert coordinator.last_update_success


async def test_first_refresh_failure_still_retries_setup(hass, unifi):
    async def boom(req):
        raise RuntimeError("down")
    unifi.runtime_data.api.request = boom
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=[
        ConfigSubentryData(subentry_type="access_point", title="Hallway AP", unique_id=None,
            data={"driver": "unifi_network", "ap_mac": AP, "name": ""})])
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_bad_ap_mac_subentry_is_skipped_not_fatal(hass, unifi):
    bad = ConfigSubentryData(subentry_type="access_point", title="Broken", unique_id=None,
        data={"driver": "unifi_network", "ap_mac": "garbage", "name": ""})
    entry = await _setup(hass, unifi, extra=[bad])
    assert entry.state is ConfigEntryState.LOADED
    assert [ap.title for ap in entry.runtime_data.access_points] == ["Hallway AP", "Garden AP"]


async def test_bad_ap_mac_in_flow_reports_field_error(hass, unifi):
    from custom_components.wifi_association_presence.config_flow import _async_build_and_test
    from wifi_ap_associations.unifi_network import UnifiNetworkDriver
    assert await _async_build_and_test(hass, UnifiNetworkDriver, {"ap_mac": "zz:zz"}) == ({"ap_mac": "invalid_value"}, None)
    assert await _async_build_and_test(hass, UnifiNetworkDriver, {}) == ({"ap_mac": "invalid_value"}, None)


async def test_unifi_ap_has_no_device_and_uses_unifi_device_area(hass, unifi):
    from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
    reg = dr.async_get(hass)
    kitchen = ar.async_get(hass).async_get_or_create("Kitchen")
    # the UniFi integration's own device for the AP, as it registers it (lowercase MAC)
    unifi_dev = reg.async_get_or_create(config_entry_id=unifi.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "78:8a:20:00:00:10")},
        name="Hallway AP", manufacturer="Ubiquiti Networks", model="U7PG2")
    before = len(reg.devices)
    entry = await _setup(hass, unifi)
    sub = entry.runtime_data.access_points[0].subentry_id
    assert reg.async_get_device_by_identifier((DOMAIN, sub), entry.entry_id) is None
    assert len(reg.devices) == before + 1  # only the tracked phone
    ap_entities = [e for e in er.async_get(hass).entities.values()
                   if e.config_entry_id == entry.entry_id and e.config_subentry_id == sub]
    assert ap_entities == []
    assert entry.runtime_data.access_point_device_ids() == {unifi_dev.id}
    # area set on UniFi's device drives the phone's Area sensor, and nothing else does
    with patch("custom_components.wifi_association_presence.sensor.TrackedDeviceSensor.async_write_ha_state") as w:
        reg.async_update_device(unifi_dev.id, sw_version="9")
        await hass.async_block_till_done()
        assert w.call_count == 0
    reg.async_update_device(unifi_dev.id, area_id=kitchen.id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.phone_area").state == "Kitchen"
    assert hass.states.get("sensor.phone_area").attributes["area_id"] == kitchen.id


async def test_stale_own_device_removed_on_upgrade(hass, unifi):
    from homeassistant.helpers import device_registry as dr
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=[
        ConfigSubentryData(subentry_type="access_point", title="Hallway AP", unique_id=None,
            data={"driver": "unifi_network", "ap_mac": AP, "name": ""})])
    entry.add_to_hass(hass)
    sub = next(iter(entry.subentries))
    reg = dr.async_get(hass)
    stale = reg.async_get_or_create(config_entry_id=entry.entry_id, config_subentry_id=sub,
        identifiers={(DOMAIN, sub)}, name="Hallway AP")  # as 0.5.0-pre created it
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get(stale.id) is None
