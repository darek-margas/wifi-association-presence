"""The "Collect access point report" button, then Download diagnostics."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, UnknownFlow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from wifi_ap_associations import AccessPointAuthError, AccessPointError
from custom_components.wifi_association_presence.const import DATA_AP_REPORT, DOMAIN
from custom_components.wifi_association_presence.diagnostics import (
    NO_REPORT,
    async_get_config_entry_diagnostics,
)

FLOW = "custom_components.wifi_association_presence.config_flow"
SSH_FORM = {
    "host": "192.168.1.23",
    "port": 22,
    "username": "admin",
    "password": "hunter2",
    "profile": "dlink_dap",
    "commands": "show station\n\n",
    "legacy_ssh": True,
}
V2C_FORM = {"host": "192.168.1.23", "port": 161, "community": "s3cret-ro"}
V3_FORM = {
    "host": "192.168.1.23",
    "port": 161,
    "username": "ro",
    "auth_key": "authsecret",
    "auth_protocol": "sha256",
    "priv_key": "",
    "priv_protocol": "aes",
}
REPORT = "# READ THIS REPORT BEFORE SHARING IT\n# --- get clientinfo ---\n5C:AD:BA:XX:XX:01  -61"


async def setup_hub(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def start(hass: HomeAssistant, entry: MockConfigEntry, method: str, form: dict) -> dict:
    """Button -> method menu -> form -> submit; returns the result of submitting.

    Usually the progress step; when the collection finishes at once (as a fake collector
    does), Home Assistant may already have run the flow on to its result.
    """
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, "access_point_report"), context={"source": "user"})
    assert result["type"] is FlowResultType.MENU
    result = await flows.async_configure(result["flow_id"], {"next_step_id": method})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == method
    return await flows.async_configure(result["flow_id"], form)


async def finish(hass: HomeAssistant, result: dict) -> dict[str, Any] | None:
    """Wait for the collection; the flow's last result, or None if it ended by itself."""
    if result["type"] is not FlowResultType.SHOW_PROGRESS:
        return result  # already finished while submitting
    await hass.async_block_till_done()
    try:
        return await hass.config_entries.subentries.async_configure(result["flow_id"])
    except UnknownFlow:
        return None


def is_ready(result: dict[str, Any] | None) -> bool:
    return result is None or (
        result["type"] is FlowResultType.ABORT and result["reason"] == "report_ready"
    )


def close(hass: HomeAssistant, result: dict[str, Any]) -> None:
    """Close a flow left open on a form, as a user closing the dialog does."""
    try:
        hass.config_entries.subentries.async_abort(result["flow_id"])
    except UnknownFlow:
        pass


async def test_ssh_report_is_in_diagnostics(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    collector = AsyncMock(return_value=REPORT)
    with patch(f"{FLOW}.async_collect_ssh_report", collector):
        result = await finish(hass, await start(hass, entry, "ssh", SSH_FORM))

    assert is_ready(result), result
    collector.assert_awaited_once_with(
        "192.168.1.23",
        "admin",
        "hunter2",
        port=22,
        profile="dlink_dap",
        commands=["show station"],
        legacy_ssh=True,
    )
    assert entry.options == {} and not entry.subentries  # nothing changed or added

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    report = diagnostics["access_point_report"]
    assert report["report"] == REPORT.splitlines()
    assert report["host"] == "192.x.x.23" and report["method"] == "ssh"
    assert report["port"] == 22 and report["profile"] == "dlink_dap"
    assert report["extra_commands"] == ["show station"] and report["legacy_ssh"] is True
    assert report["last_attempt"]["result"] == "ok"
    assert "hunter2" not in str(diagnostics)


async def test_snmp_v2c_report(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    collector = AsyncMock(return_value=REPORT)
    with (
        patch(f"{FLOW}.async_collect_snmp_report", collector),
        patch(f"{FLOW}.async_import_module", AsyncMock()),
    ):
        result = await finish(hass, await start(hass, entry, "snmp_v2c", V2C_FORM))

    assert is_ready(result), result
    collector.assert_awaited_once_with(
        "192.168.1.23", port=161, community="s3cret-ro", max_values=5000
    )
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    report = diagnostics["access_point_report"]
    assert report["method"] == "snmp-v2c" and report["port"] == 161
    assert report["max_values_per_table"] == 5000
    assert "s3cret-ro" not in str(diagnostics)


async def test_snmp_v3_report_keeps_no_keys(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    collector = AsyncMock(return_value=REPORT)
    with (
        patch(f"{FLOW}.async_collect_snmp_report", collector),
        patch(f"{FLOW}.async_import_module", AsyncMock()),
    ):
        result = await finish(hass, await start(hass, entry, "snmp_v3", V3_FORM))

    assert is_ready(result), result
    collector.assert_awaited_once_with(
        "192.168.1.23",
        port=161,
        user="ro",
        auth_key="authsecret",
        priv_key="",
        auth_protocol="sha256",
        priv_protocol="aes",
        max_values=5000,
    )
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    report = diagnostics["access_point_report"]
    assert report["method"] == "snmp-v3"
    assert (report["auth_protocol"], report["priv_protocol"]) == ("sha256", "aes")
    assert "authsecret" not in str(diagnostics)


async def test_rejected_login_shows_error_and_is_recorded(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    with patch(f"{FLOW}.async_collect_ssh_report", AsyncMock(side_effect=AccessPointAuthError("x"))):
        result = await finish(hass, await start(hass, entry, "ssh", SSH_FORM))

    assert result is not None
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "ssh"
    assert result["errors"] == {"base": "invalid_auth"}
    stored = hass.data[DATA_AP_REPORT]
    assert stored["last_attempt"]["result"] == "failed: invalid_auth"
    assert "report" not in stored
    close(hass, result)


async def test_old_algorithms_suggest_legacy_ssh(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    error = AccessPointError("connection failed: no matching kex; try legacy SSH")
    with patch(f"{FLOW}.async_collect_ssh_report", AsyncMock(side_effect=error)):
        result = await finish(hass, await start(hass, entry, "ssh", SSH_FORM))

    assert result is not None and result["errors"] == {"base": "legacy_ssh"}
    close(hass, result)


async def test_snmp_without_answer(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    with (
        patch(f"{FLOW}.async_collect_snmp_report", AsyncMock(side_effect=AccessPointError("no"))),
        patch(f"{FLOW}.async_import_module", AsyncMock()),
    ):
        result = await finish(hass, await start(hass, entry, "snmp_v2c", V2C_FORM))

    assert result is not None and result["step_id"] == "snmp_v2c"
    assert result["errors"] == {"base": "snmp_no_answer"}
    close(hass, result)


async def test_closing_the_window_does_not_stop_the_collection(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    release = asyncio.Event()

    async def slow_collector(*args: Any, **kwargs: Any) -> str:
        await release.wait()
        return REPORT

    with patch(f"{FLOW}.async_collect_ssh_report", slow_collector):
        progress = await start(hass, entry, "ssh", SSH_FORM)
        assert progress["type"] is FlowResultType.SHOW_PROGRESS  # the fake waits
        hass.config_entries.subentries.async_abort(progress["flow_id"])  # window closed
        await hass.async_block_till_done()
        assert DATA_AP_REPORT not in hass.data
        release.set()
        # The collection is a background task: block_till_done waits for it only on request.
        await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.data[DATA_AP_REPORT]["report"] == REPORT.splitlines()


async def test_grace_period_is_the_options_form(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"consider_home": 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"consider_home": 60}


async def test_diagnostics_without_report(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["access_point_report"] == NO_REPORT
    assert diagnostics["access_point_types"] == []
    assert diagnostics["tracked_devices"] == 0
