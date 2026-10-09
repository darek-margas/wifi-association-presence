"""The "Collect access point report" button, then Download diagnostics."""

from __future__ import annotations

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

COLLECTOR = "custom_components.wifi_association_presence.config_flow.async_collect_ssh_report"
FORM = {
    "host": "192.168.1.23",
    "port": 22,
    "username": "admin",
    "password": "hunter2",
    "profile": "dlink_dap",
    "commands": "show station\n\n",
    "legacy_ssh": True,
}
REPORT = "# READ THIS REPORT BEFORE SHARING IT\n# --- get clientinfo ---\n5C:AD:BA:XX:XX:01  -61"


async def setup_hub(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def collect(hass: HomeAssistant, entry: MockConfigEntry, collector: AsyncMock) -> dict[str, Any] | None:
    """Button -> form -> submit -> wait for the progress step; the flow's last result.

    None when the flow finished by itself after the collection (Home Assistant runs the
    step after a finished progress task on its own).
    """
    with patch(COLLECTOR, collector):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "access_point_report"), context={"source": "user"}
        )
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
        result = await hass.config_entries.subentries.async_configure(result["flow_id"], FORM)
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await hass.async_block_till_done()
        try:
            return await hass.config_entries.subentries.async_configure(result["flow_id"])
        except UnknownFlow:
            return None


async def test_collected_report_is_in_diagnostics(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    collector = AsyncMock(return_value=REPORT)
    result = await collect(hass, entry, collector)

    assert result is None or (
        result["type"] is FlowResultType.ABORT and result["reason"] == "report_ready"
    ), result
    collector.assert_awaited_once_with(
        "192.168.1.23",
        "admin",
        "hunter2",
        port=22,
        profile="dlink_dap",
        commands=["show station"],
        legacy_ssh=True,
    )
    assert entry.options == {}  # collecting changes no settings
    assert not entry.subentries  # and adds nothing

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    report = diagnostics["access_point_report"]
    assert report["report"] == REPORT.splitlines()
    assert report["host"] == "192.x.x.23"
    assert report["profile"] == "dlink_dap" and report["legacy_ssh"] is True
    assert "hunter2" not in str(diagnostics)


async def test_rejected_login_shows_error_and_keeps_no_report(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    result = await collect(hass, entry, AsyncMock(side_effect=AccessPointAuthError("rejected")))

    assert result is not None
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}
    assert DATA_AP_REPORT not in hass.data


async def test_old_algorithms_suggest_legacy_ssh(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    error = AccessPointError("connection failed: no matching kex; try legacy SSH")
    result = await collect(hass, entry, AsyncMock(side_effect=error))

    assert result is not None and result["errors"] == {"base": "legacy_ssh"}


async def test_unreachable_is_cannot_connect(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    result = await collect(hass, entry, AsyncMock(side_effect=AccessPointError("refused")))

    assert result is not None and result["errors"] == {"base": "cannot_connect"}


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
