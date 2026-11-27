"""The Restart button on access points whose driver can restart them."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from wifi_ap_associations import DRIVERS, AccessPointError

from test_core_ha import FakeDriver, ap, fake_driver, setup_entry  # noqa: F401


class RestartingDriver(FakeDriver):
    RESTARTS = True
    restarts: list[str] = []
    fail: Exception | None = None

    async def async_restart(self) -> None:
        if RestartingDriver.fail is not None:
            raise RestartingDriver.fail
        RestartingDriver.restarts.append(self.config["host"])


@pytest.fixture
def restarting(fake_driver):  # noqa: F811
    DRIVERS["fake"] = RestartingDriver
    RestartingDriver.restarts = []
    RestartingDriver.fail = None
    yield RestartingDriver
    DRIVERS["fake"] = FakeDriver


async def test_restart_button(
    hass: HomeAssistant, restarting, caplog: pytest.LogCaptureFixture
) -> None:
    await setup_entry(hass, ap("ap1"))
    entity = er.async_get(hass).async_get("button.hallway_ap_restart")
    assert entity is not None and entity.entity_category == er.EntityCategory.CONFIG
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.hallway_ap_restart"}, blocking=True
    )
    assert restarting.restarts == ["ap1"]

    # A failure shows in the dialog and is logged with its reason.
    restarting.fail = AccessPointError("192.0.2.1: web login rejected")
    with pytest.raises(HomeAssistantError, match="Could not restart"):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.hallway_ap_restart"}, blocking=True
        )
    assert "Restarting access point Hallway AP failed: 192.0.2.1: web login rejected" in caplog.text


async def test_no_button_for_drivers_that_cannot(hass: HomeAssistant) -> None:
    await setup_entry(hass, ap("ap1"))
    assert hass.states.get("button.hallway_ap_restart") is None
