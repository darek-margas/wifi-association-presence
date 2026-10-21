"""UniFi and TP-Link Omada access points: their own controller login (since 0.12).

The controllers themselves are the library's business (its tests serve fake ones over
HTTP); here the reads and the access point list are stand-ins, and what's tested is
the Home Assistant side: the two-step form, the repair notice and area carry-over for
access points saved by earlier versions, and closing the sessions on unload.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from wifi_ap_associations import AccessPointAuthError

DOMAIN = "wifi_association_presence"
AP = "78:8A:20:00:00:10"
GARDEN = "78:8A:20:00:00:20"
PHONE = "5C:AD:BA:00:00:01"
UNIFI = "wifi_ap_associations.unifi_network"
OMADA = "wifi_ap_associations.omada_network"
LOGIN = {
    "host": "192.0.2.1",
    "port": 443,
    "username": "ro",
    "password": "pw",
    "site": "default",
    "verify_ssl": "no",
}
DEVICES = [
    {"mac": "78:8a:20:00:00:10", "type": "uap", "model": "U7PG2", "name": "Hallway AP",
     "version": "6.6.77", "state": 1, "uptime": 3600, "system-stats": {"cpu": "5", "mem": "40"}},
    {"mac": "78:8a:20:00:00:20", "type": "uap", "model": "U6LR", "name": "Garden AP", "state": 1},
]
CLIENTS = [
    {"mac": "5c:ad:ba:00:00:01", "is_wired": False, "ap_mac": "78:8a:20:00:00:10",
     "essid": "Home", "radio": "na", "signal": -58, "uptime": 100},
]


class unifi_read:
    """The controller's read, and a quiet event stream (no sockets in tests)."""

    def __enter__(self):
        async def read(self):
            return DEVICES, CLIENTS

        async def watch(self, callback):
            await asyncio.Event().wait()

        self._patches = [
            patch(f"{UNIFI}.UnifiController.read", read),
            patch(f"{UNIFI}.UnifiController.watch", watch),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


def ap_sub(mac: str, title: str, **data) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type="access_point", title=title, unique_id=None,
        data={"driver": "unifi_network", "ap_mac": mac, "name": "", **data},
    )


def phone_sub() -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type="tracked_device", title="Phone", unique_id=PHONE, data={"mac": PHONE}
    )


async def _hub(hass: HomeAssistant, *subs: ConfigSubentryData) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=list(subs))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_presence_through_the_controller(hass: HomeAssistant) -> None:
    with unifi_read():
        entry = await _hub(hass, ap_sub(AP, "Hallway AP", **LOGIN), phone_sub())
        assert entry.state is ConfigEntryState.LOADED
        assert hass.states.get("device_tracker.phone").state == "home"
        # Its own device now, not the UniFi integration's.
        device = dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, next(iter(entry.subentries))), entry.entry_id
        )
        assert device is not None and device.model == "U7PG2"
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_add_logs_in_then_picks_the_access_point(hass: HomeAssistant) -> None:
    async def listing(cls, config):
        assert config["host"] == "192.0.2.1" and "ap_mac" not in config
        return [(AP, "Hallway AP (U7PG2, 78:8A:20:00:00:10)"), (GARDEN, "Garden AP (U6LR, 78:8A:20:00:00:20)")]

    with unifi_read(), patch(f"{UNIFI}.UnifiNetworkDriver.async_list_access_points", classmethod(listing)):
        entry = await _hub(hass, ap_sub(GARDEN, "Garden AP", **LOGIN))
        flows = hass.config_entries.subentries
        result = await flows.async_init((entry.entry_id, "access_point"), context={"source": "user"})
        result = await flows.async_configure(result["flow_id"], {"driver": "unifi_network"})
        assert result["step_id"] == "details"
        assert "ap_mac" not in {str(k) for k in result["data_schema"].schema}
        result = await flows.async_configure(result["flow_id"], LOGIN)
        assert result["step_id"] == "pick"
        options = next(iter(result["data_schema"].schema.values())).config["options"]
        assert [o["value"] for o in options] == [AP]  # the Garden AP is added already
        result = await flows.async_configure(result["flow_id"], {"ap_mac": AP})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Hallway AP"
        assert result["data"] == {"driver": "unifi_network", **LOGIN, "name": "", "ap_mac": AP}
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_rejected_login_says_why(hass: HomeAssistant) -> None:
    async def listing(cls, config):
        raise AccessPointAuthError("192.0.2.1: the controller rejected the user or password")

    with patch(f"{UNIFI}.UnifiNetworkDriver.async_list_access_points", classmethod(listing)):
        entry = await _hub(hass)
        flows = hass.config_entries.subentries
        result = await flows.async_init((entry.entry_id, "access_point"), context={"source": "user"})
        result = await flows.async_configure(result["flow_id"], {"driver": "unifi_network"})
        result = await flows.async_configure(result["flow_id"], {**LOGIN, "password": "nope"})
    assert result["step_id"] == "details" and result["errors"] == {"base": "invalid_auth"}
    assert "rejected the user or password" in result["description_placeholders"]["reason"]


async def test_nothing_left_to_add(hass: HomeAssistant) -> None:
    async def listing(cls, config):
        return [(AP, "Hallway AP")]

    with unifi_read(), patch(f"{UNIFI}.UnifiNetworkDriver.async_list_access_points", classmethod(listing)):
        entry = await _hub(hass, ap_sub(AP, "Hallway AP", **LOGIN))
        flows = hass.config_entries.subentries
        result = await flows.async_init((entry.entry_id, "access_point"), context={"source": "user"})
        result = await flows.async_configure(result["flow_id"], {"driver": "unifi_network"})
        result = await flows.async_configure(result["flow_id"], LOGIN)
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "no_access_points_left"
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_access_point_saved_before_0_12(hass: HomeAssistant) -> None:
    """Saved when it read through the UniFi integration: just the MAC. It asks for the
    login with a repair notice, keeps its area, and works again once reconfigured."""
    kitchen = ar.async_get(hass).async_get_or_create("Kitchen")
    unifi_entry = MockConfigEntry(domain="unifi", title="UniFi")
    unifi_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    theirs = registry.async_get_or_create(
        config_entry_id=unifi_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, AP.lower())},
        name="Hallway AP",
    )
    registry.async_update_device(theirs.id, area_id=kitchen.id)

    with unifi_read():
        # Next to an access point that works: the old one fails, not the entry.
        entry = await _hub(
            hass, ap_sub(AP, "Hallway AP"), ap_sub(GARDEN, "Garden AP", **LOGIN), phone_sub()
        )
        assert entry.state is ConfigEntryState.LOADED
        sub_id = next(s for s, sub in entry.subentries.items() if sub.data.get("ap_mac") == AP)
        issue = ir.async_get(hass).async_get_issue(DOMAIN, f"controller_login_{sub_id}")
        assert issue is not None and issue.translation_key == "controller_login"
        ours = registry.async_get_device_by_identifier((DOMAIN, sub_id), entry.entry_id)
        assert ours is not None and ours.area_id == kitchen.id
        assert not entry.runtime_data.data.access_points[sub_id].available

        result = await entry.start_subentry_reconfigure_flow(hass, sub_id)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {**LOGIN, "ap_mac": AP}
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert entry.subentries[sub_id].data["host"] == "192.0.2.1"
        assert ir.async_get(hass).async_get_issue(DOMAIN, f"controller_login_{sub_id}") is None
        assert hass.states.get("device_tracker.phone").state == "home"
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_closes_the_controller_sessions(hass: HomeAssistant) -> None:
    closed: list[str] = []

    async def close(self):
        closed.append(self.ap_mac)

    with unifi_read(), patch(f"{UNIFI}.UnifiNetworkDriver.async_close", close):
        entry = await _hub(hass, ap_sub(AP, "Hallway AP", **LOGIN), ap_sub(GARDEN, "Garden AP", **LOGIN))
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert sorted(closed) == [AP, GARDEN]


async def test_omada_through_its_controller(hass: HomeAssistant) -> None:
    omada_ap = "C0:C9:E3:4B:AF:0E"

    async def read(self):
        return (
            [{"mac": "C0-C9-E3-4B-AF-0E", "type": "ap", "name": "Spare Room", "model": "EAP653",
              "statusCategory": 1, "uptimeLong": 100}],
            [{"mac": "5C-AD-BA-00-00-01", "wireless": True, "active": True, "apMac": "C0-C9-E3-4B-AF-0E",
              "ssid": "Home", "radioId": 1, "rssi": -60, "uptime": 50}],
        )

    async def listing(cls, config):
        return [(omada_ap, "Spare Room (EAP653, C0:C9:E3:4B:AF:0E)")]

    with patch(f"{OMADA}.OmadaController.read", read), patch(
        f"{OMADA}.OmadaNetworkDriver.async_list_access_points", classmethod(listing)
    ):
        entry = await _hub(hass, phone_sub())
        flows = hass.config_entries.subentries
        result = await flows.async_init((entry.entry_id, "access_point"), context={"source": "user"})
        result = await flows.async_configure(result["flow_id"], {"driver": "omada_network"})
        result = await flows.async_configure(result["flow_id"], {**LOGIN, "port": 8043, "site": "Default"})
        assert result["step_id"] == "pick"
        result = await flows.async_configure(result["flow_id"], {"ap_mac": omada_ap})
        assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "Spare Room"
        await hass.async_block_till_done()
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get("device_tracker.phone").state == "home"
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_only_old_access_points_still_get_the_notice(hass: HomeAssistant) -> None:
    """Nothing can be read, so setup retries; the repair notice says why."""
    with unifi_read():
        entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=[ap_sub(AP, "Hallway AP")])
        entry.add_to_hass(hass)
        assert not await hass.config_entries.async_setup(entry.entry_id)
        assert entry.state is ConfigEntryState.SETUP_RETRY
        sub_id = next(iter(entry.subentries))
        assert ir.async_get(hass).async_get_issue(DOMAIN, f"controller_login_{sub_id}") is not None


async def test_a_failed_first_poll_releases_the_controller_sessions(hass: HomeAssistant) -> None:
    """Review of 0.12.0b1: setup retries without an unload, so each attempt closes its
    drivers itself; else every retry would keep one more user of the session."""
    from wifi_ap_associations import AccessPointError

    closed: list[str] = []

    async def read(self):
        raise AccessPointError("controller unreachable")

    async def close(self):
        closed.append(self.ap_mac)

    with patch(f"{UNIFI}.UnifiController.read", read), patch(
        f"{UNIFI}.UnifiNetworkDriver.async_close", close
    ):
        entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=[
            ap_sub(AP, "Hallway AP", **LOGIN), ap_sub(GARDEN, "Garden AP", **LOGIN)])
        entry.add_to_hass(hass)
        assert not await hass.config_entries.async_setup(entry.entry_id)
        assert entry.state is ConfigEntryState.SETUP_RETRY
    assert sorted(closed) == [AP, GARDEN]


async def test_upgrade_of_a_named_access_point_without_an_area(hass: HomeAssistant) -> None:
    """Review of 0.12.0b1: an AP the user named, whose UniFi device has no area (nor any
    device at all): its own device keeps the name, gets no area, and its entity IDs stay
    the same through the Reconfigure that adds the login."""
    from homeassistant.helpers import entity_registry as er

    with unifi_read():
        named = ConfigSubentryData(
            subentry_type="access_point", title="Porch", unique_id=None,
            data={"driver": "unifi_network", "ap_mac": AP, "name": "Porch"},
        )
        entry = await _hub(hass, named, ap_sub(GARDEN, "Garden AP", **LOGIN), phone_sub())
        sub_id = next(s for s, sub in entry.subentries.items() if sub.data.get("ap_mac") == AP)
        registry = dr.async_get(hass)
        device = registry.async_get_device_by_identifier((DOMAIN, sub_id), entry.entry_id)
        assert device is not None and device.name == "Porch" and device.area_id is None
        entities = er.async_get(hass)
        before = sorted(
            e.entity_id for e in er.async_entries_for_config_entry(entities, entry.entry_id)
            if e.config_subentry_id == sub_id
        )
        assert before  # the AP's own sensors

        result = await entry.start_subentry_reconfigure_flow(hass, sub_id)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {**LOGIN, "ap_mac": AP, "name": "Porch"}
        )
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        after = sorted(
            e.entity_id for e in er.async_entries_for_config_entry(entities, entry.entry_id)
            if e.config_subentry_id == sub_id
        )
        assert after == before
        device = registry.async_get_device_by_identifier((DOMAIN, sub_id), entry.entry_id)
        assert device.name == "Porch" and device.area_id is None
        assert entry.runtime_data.data.access_points[sub_id].available
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_removing_an_old_access_point_removes_its_notice(hass: HomeAssistant) -> None:
    """Local review: the notice of an access point deleted before it was reconfigured
    must go with it, and all of them with the integration."""
    with unifi_read():
        shed = "78:8A:20:00:00:30"
        entry = await _hub(hass, ap_sub(AP, "Hallway AP"), ap_sub(shed, "Shed AP"),
                           ap_sub(GARDEN, "Garden AP", **LOGIN))
        issues = ir.async_get(hass)
        by_mac = {sub.data["ap_mac"]: s for s, sub in entry.subentries.items()}
        hallway, garden = by_mac[AP], by_mac[shed]
        assert issues.async_get_issue(DOMAIN, f"controller_login_{hallway}") is not None
        assert hass.config_entries.async_remove_subentry(entry, hallway)
        await hass.async_block_till_done()
        assert issues.async_get_issue(DOMAIN, f"controller_login_{hallway}") is None
        assert issues.async_get_issue(DOMAIN, f"controller_login_{garden}") is not None
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        assert issues.async_get_issue(DOMAIN, f"controller_login_{garden}") is None
