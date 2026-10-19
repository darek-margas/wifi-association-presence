"""Instant arrivals: an access point's change feed makes the integration poll at once."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from wifi_ap_associations import (
    DRIVERS,
    AccessPointDriver,
    AccessPointError,
    AccessPointInfo,
    AssociatedClient,
    DriverField,
    PollResult,
)

DOMAIN = "wifi_association_presence"
PHONE = "5C:AD:BA:00:00:01"


class WatchingDriver(AccessPointDriver):
    """An access point whose feed reports one change, or fails, as the test says."""

    TYPE = "fake_watch"
    NAME = "Fake watching AP"
    FIELDS = (DriverField("host"),)
    SIGNAL_UNIT = "dBm"
    WATCHES = True
    polls = 0
    watches = 0
    fail = False

    async def async_get_associated_clients(self) -> list[AssociatedClient]:
        return (await self.async_poll()).clients

    async def async_poll(self) -> PollResult:
        WatchingDriver.polls += 1
        return PollResult([AssociatedClient(PHONE, "Home", "5GHz", -50)], AccessPointInfo(name="AP"))

    burst = 1

    leave_given: list[bool] = []

    async def async_watch(self, on_change: Callable[[], None], on_leave=None) -> None:
        WatchingDriver.leave_given.append(on_leave is not None)
        WatchingDriver.watches += 1
        if WatchingDriver.fail and WatchingDriver.watches == 1:
            raise AccessPointError("feed lost")  # only the first try fails
        for _ in range(WatchingDriver.burst):
            on_change()
        await asyncio.Event().wait()


@pytest.fixture(autouse=True)
def watching_driver():
    DRIVERS["fake_watch"] = WatchingDriver
    WatchingDriver.polls = WatchingDriver.watches = 0
    WatchingDriver.leave_given = []
    WatchingDriver.fail = False
    WatchingDriver.burst = 1
    yield WatchingDriver
    DRIVERS.pop("fake_watch")


async def setup(hass: HomeAssistant, options: dict[str, Any] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wi-Fi",
        data={},
        options=options or {},
        subentries_data=[
            ConfigSubentryData(
                subentry_type="access_point", title="AP", unique_id=None,
                data={"driver": "fake_watch", "host": "ap1", "name": ""},
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await asyncio.sleep(0)  # let the watch task run its first step
    await hass.async_block_till_done()
    return entry


async def test_a_change_polls_at_once(hass: HomeAssistant) -> None:
    entry = await setup(hass)
    assert WatchingDriver.watches == 1
    # The first refresh at setup, then one asked for by the change.
    assert WatchingDriver.polls == 2
    assert await hass.config_entries.async_unload(entry.entry_id)  # ends the watch


async def test_option_off_means_no_watch(hass: HomeAssistant) -> None:
    entry = await setup(hass, {"instant_arrivals": False})
    assert WatchingDriver.watches == 0
    assert WatchingDriver.polls == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_failing_feed_is_tried_again_later(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.wifi_association_presence import coordinator

    monkeypatch.setattr(coordinator, "WATCH_RETRY_MIN", 0)  # retry at once
    WatchingDriver.fail = True
    entry = await setup(hass)
    for _ in range(10):
        await asyncio.sleep(0)
        await hass.async_block_till_done()
    # The first watch failed (polling went on: the setup poll), the retry reported a change.
    assert WatchingDriver.watches == 2
    assert WatchingDriver.polls == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_burst_of_changes_is_one_task_and_one_poll(hass: HomeAssistant) -> None:
    WatchingDriver.burst = 20
    entry = await setup(hass)
    coordinator = entry.runtime_data
    # The setup poll, then one for the whole burst (the rest fall in the cooldown).
    assert WatchingDriver.polls == 2
    assert coordinator._trigger_task is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_leaves_are_asked_for_only_with_fast_departure(hass: HomeAssistant) -> None:
    entry = await setup(hass)
    assert WatchingDriver.leave_given == [False]
    assert entry.runtime_data.tracker.fast_departure is None
    assert await hass.config_entries.async_unload(entry.entry_id)

    WatchingDriver.leave_given = []
    entry = await setup(hass, {"fast_departure": 30})
    assert WatchingDriver.leave_given == [True]
    coordinator = entry.runtime_data
    assert coordinator.tracker.fast_departure.total_seconds() == 30
    # A leave notes it in the tracker and schedules the poll at the deadline.
    ap = coordinator.access_points[0]
    coordinator._on_leave(ap)
    assert ap.subentry_id in coordinator._departure_polls
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert coordinator._departure_polls == {}
