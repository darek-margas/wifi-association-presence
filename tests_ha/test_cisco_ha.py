"""Cisco controller APs in the access point form: identity and SNMP version switching."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from wifi_ap_associations import AccessPointInfo, PollResult

DOMAIN = "wifi_association_presence"
FLOW = "custom_components.wifi_association_presence.config_flow"
SNMP_AP01 = {
    "driver": "cisco_wlc_snmp",
    "host": "192.0.2.20",
    "port": 161,
    "ap_name": "AP01",
    "snmp_version": "v3",
    "username": "ro",
    "auth_key": "authsecret1",
    "auth_protocol": "sha256",
    "priv_protocol": "aes",
    "name": "",
}


async def _hub(hass: HomeAssistant, *aps: dict) -> MockConfigEntry:
    """A loaded hub with these access points; every Cisco poll answers at once."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wi-Fi",
        data={},
        subentries_data=[
            ConfigSubentryData(
                subentry_type="access_point", title=ap["ap_name"], unique_id=None, data=ap
            )
            for ap in aps
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _answer():
    async def poll(self):
        return PollResult([], AccessPointInfo(name=self.config["ap_name"]))

    return (
        patch("wifi_ap_associations.cisco_wlc_snmp.CiscoWlcSnmp.async_poll", poll),
        patch("wifi_ap_associations.cisco_wlc_ssh.CiscoWlcSsh.async_poll", poll),
        patch(f"{FLOW}.async_import_module"),
    )


async def _add(hass: HomeAssistant, entry: MockConfigEntry, driver: str, form: dict):
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, "access_point"), context={"source": "user"})
    result = await flows.async_configure(result["flow_id"], {"driver": driver})
    assert result["step_id"] == "details"
    return await flows.async_configure(result["flow_id"], form)


async def test_two_controllers_may_each_have_an_ap01(hass: HomeAssistant) -> None:
    snmp, ssh, imports = _answer()
    with snmp, ssh, imports:
        entry = await _hub(hass, SNMP_AP01)
        result = await _add(
            hass, entry, "cisco_wlc_snmp",
            {"host": "192.0.2.30", "port": 161, "ap_name": "AP01", "snmp_version": "v2c",
             "community": "public"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result


async def test_same_ap_over_ssh_and_snmp_is_a_duplicate(hass: HomeAssistant) -> None:
    snmp, ssh, imports = _answer()
    with snmp, ssh, imports:
        entry = await _hub(hass, SNMP_AP01)
        result = await _add(
            hass, entry, "cisco_wlc_ssh",
            {"host": "192.0.2.20", "port": 22, "username": "ha", "password": "x",
             "ap_name": "ap01"},
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_snmp_v3_to_v2c(hass: HomeAssistant) -> None:
    snmp, ssh, imports = _answer()
    with snmp, ssh, imports:
        entry = await _hub(hass, SNMP_AP01)
        sub = next(iter(entry.subentries.values()))
        result = await entry.start_subentry_reconfigure_flow(hass, sub.subentry_id)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {"host": "192.0.2.20", "port": 161, "ap_name": "AP01", "snmp_version": "v2c",
             "community": "public", "auth_protocol": "sha256", "priv_protocol": "aes"},
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    data = entry.subentries[sub.subentry_id].data
    # The old v3 user may stay stored; the version says it is no longer used.
    assert (data["snmp_version"], data["community"]) == ("v2c", "public")
    from wifi_ap_associations.cisco_wlc_snmp import CiscoWlcSnmp
    import pysnmp.hlapi.v3arch.asyncio as hlapi

    assert isinstance(CiscoWlcSnmp(dict(data))._auth(hlapi), hlapi.CommunityData)
