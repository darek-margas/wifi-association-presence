"""Standalone TP-Link EAPs (SNMP) in the access point form."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from wifi_ap_associations import (
    DRIVERS,
    AccessPointError,
    AccessPointInfo,
    AssociatedClient,
    PollResult,
)

DOMAIN = "wifi_association_presence"
FLOW = "custom_components.wifi_association_presence.config_flow"
POLL = "wifi_ap_associations.tplink_eap_snmp.TplinkEapSnmp.async_poll"
EAP = {
    "host": "192.0.2.48",
    "port": 161,
    "snmp_version": "v2c",
    "community": "public-ro",
    "name": "",
}


async def _hub(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _add(hass: HomeAssistant, entry: MockConfigEntry, form: dict):
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, "access_point"), context={"source": "user"})
    result = await flows.async_configure(result["flow_id"], {"driver": "tplink_eap_snmp"})
    assert result["step_id"] == "details"
    return await flows.async_configure(result["flow_id"], form)


async def test_offered_as_experimental() -> None:
    assert DRIVERS["tplink_eap_snmp"].EXPERIMENTAL


async def test_add_a_standalone_eap(hass: HomeAssistant) -> None:
    async def poll(self):
        return PollResult(
            [AssociatedClient("34:85:18:00:00:01")], AccessPointInfo(name="EAP670", model="EAP670")
        )

    entry = await _hub(hass)
    with patch(POLL, poll), patch(f"{FLOW}.async_import_module"):
        result = await _add(hass, entry, EAP)
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        sub = next(iter(entry.subentries.values()))
        assert sub.data["driver"] == "tplink_eap_snmp"
        assert sub.data["community"] == "public-ro"
        # MACs only: a client count, but no per-band counts or signal sensors.
        registry = er.async_get(hass)

        def sensor(key: str) -> str | None:
            return registry.async_get_entity_id("sensor", DOMAIN, f"{sub.subentry_id}_{key}")

        assert sensor("clients_total")
        for key in ("clients_2_4ghz", "clients_5ghz", "average_client_signal", "weak_clients"):
            assert sensor(key) is None, key
        # The same EAP again is a duplicate.
        again = await _add(hass, entry, EAP)
        assert again["type"] is FlowResultType.ABORT


async def test_an_eap_without_the_table_is_refused(hass: HomeAssistant) -> None:
    async def poll(self):
        raise AccessPointError("no TP-Link EAP client table (1.3.6.1.4.1.11863.10.1.1) on this device")

    entry = await _hub(hass)
    with patch(POLL, poll), patch(f"{FLOW}.async_import_module"):
        result = await _add(hass, entry, EAP)
    assert result["type"] is FlowResultType.FORM and result["errors"]
    assert not entry.subentries
