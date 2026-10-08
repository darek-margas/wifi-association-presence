"""The integration in Home Assistant, with a fake driver standing in for an AP."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wifi_association_presence.ap_drivers import (
    DRIVERS,
    AccessPointDriver,
    AccessPointError,
    AccessPointInfo,
    AssociatedClient,
    DriverField,
    PollResult,
)
from custom_components.wifi_association_presence.config_flow import _async_build_and_test
from custom_components.wifi_association_presence.const import DOMAIN

PHONE = "5C:AD:BA:00:00:01"


class FakeDriver(AccessPointDriver):
    """Replies from a dict, keyed by host: a PollResult or an exception to raise."""

    TYPE = "fake"
    NAME = "Fake AP"
    MANUFACTURER = "Test"
    FIELDS = (DriverField("host"),)
    SIGNAL_UNIT = "%"
    results: dict[str, Any] = {}

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        if config["host"] == "bad":
            raise ValueError("bad host")

    async def async_get_associated_clients(self) -> list[AssociatedClient]:
        return (await self.async_poll()).clients

    async def async_poll(self) -> PollResult:
        result = self.results[self.config["host"]]
        if isinstance(result, Exception):
            raise result
        return result


GOOD = PollResult(
    [AssociatedClient(PHONE, "Home", "5GHz", 90)],
    AccessPointInfo(name="Hallway AP", model="X1", firmware="1.0", cpu_percent=5, memory_percent=40),
)


@pytest.fixture(autouse=True)
def fake_driver():
    DRIVERS["fake"] = FakeDriver
    FakeDriver.results = {"ap1": GOOD}
    yield FakeDriver
    DRIVERS.pop("fake")


def ap(host: str, title: str = "Hallway AP") -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type="access_point", title=title, unique_id=None,
        data={"driver": "fake", "host": host, "name": ""},
    )


PHONE_SUB = ConfigSubentryData(
    subentry_type="tracked_device", title="Phone", unique_id=PHONE, data={"mac": PHONE}
)


async def setup_entry(
    hass: HomeAssistant, *subentries: ConfigSubentryData, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Wi-Fi", data={}, options=options or {}, subentries_data=list(subentries)
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_tracker_and_ap_sensors(hass: HomeAssistant) -> None:
    await setup_entry(hass, ap("ap1"), PHONE_SUB)
    assert hass.states.get("device_tracker.phone").state == "home"
    assert hass.states.get("sensor.phone_access_point").state == "Hallway AP"
    assert hass.states.get("sensor.hallway_ap_clients").state == "1"
    assert hass.states.get("sensor.hallway_ap_clients_5_ghz").state == "1"
    # CPU and memory exist but are opt-in: a recorder row per AP per minute otherwise
    registry = er.async_get(hass)
    for key in ("cpu", "memory"):
        entity = registry.async_get(f"sensor.hallway_ap_{key}")
        assert entity is not None and entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert hass.states.get(f"sensor.hallway_ap_{key}") is None


async def test_only_reported_details_get_sensors(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A driver without location or CPU (like OpenWrt): no sensors for them, and ones
    # left over from earlier versions are removed.
    monkeypatch.setattr(
        FakeDriver, "REPORTS", frozenset({"name", "uptime_seconds", "memory_percent"})
    )
    entry = MockConfigEntry(
        domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=[ap("ap1"), PHONE_SUB]
    )
    entry.add_to_hass(hass)
    ap_id = next(
        s.subentry_id for s in entry.subentries.values() if s.subentry_type == "access_point"
    )
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "sensor", DOMAIN, f"{ap_id}_location", config_entry=entry
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(stale.entity_id) is None
    keys = {
        e.unique_id.removeprefix(f"{ap_id}_")
        for e in registry.entities.values()
        if e.config_entry_id == entry.entry_id and e.unique_id.startswith(ap_id)
    }
    assert keys == {"clients_2_4ghz", "clients_5ghz", "clients_total", "memory", "last_boot"}


async def test_stop_saves_and_restart_keeps_visit(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    entry = await setup_entry(hass, ap("ap1"), PHONE_SUB)
    arrived_at = hass.states.get("device_tracker.phone").attributes["arrived_at"]
    key = f"{DOMAIN}.{entry.entry_id}.sightings"
    # Polls go on for a while (well past the first periodic write, 10 min apart)...
    for _ in range(5):
        await refresh_after(hass, freezer, entry.runtime_data, 60)
    # ...then Home Assistant stops. It does not unload entries then: the integration
    # must save itself, or the visit started 5 min ago would be lost.
    hass.bus.async_fire("homeassistant_stop")
    await hass.async_block_till_done()
    stored = hass_storage[key]["data"]
    assert stored["saved_at"] == dt_util.utcnow().isoformat()
    assert stored["sightings"][PHONE]["arrived"] == arrived_at
    # A restart taking longer than the visit gap must not start a new visit: being down
    # is no evidence that anyone left.
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(timedelta(minutes=5))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    tracker = hass.states.get("device_tracker.phone")
    assert tracker.state == "home"
    assert tracker.attributes["arrived_at"] == arrived_at
    # ...but a device that had already left before the stop starts afresh when it returns
    FakeDriver.results["ap1"] = PollResult([], GOOD.info)
    coordinator = entry.runtime_data
    await refresh_after(hass, freezer, coordinator, 61)
    await refresh_after(hass, freezer, coordinator, 61)
    await refresh_after(hass, freezer, coordinator, 61)
    assert hass.states.get("device_tracker.phone").state == "not_home"
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(timedelta(minutes=5))
    FakeDriver.results["ap1"] = GOOD
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    tracker = hass.states.get("device_tracker.phone")
    assert tracker.state == "home"
    assert tracker.attributes["arrived_at"] == dt_util.utcnow().isoformat()


async def test_unclean_shutdown_starts_new_visits(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    entry = await setup_entry(hass, ap("ap1"), PHONE_SUB)
    arrived_at = hass.states.get("device_tracker.phone").attributes["arrived_at"]
    key = f"{DOMAIN}.{entry.entry_id}.sightings"
    assert await hass.config_entries.async_unload(entry.entry_id)
    stored = hass_storage[key]["data"]
    assert stored["clean"] is True  # the write at unload / stop
    # Turn it into what a crash or power cut leaves behind: the last periodic write.
    stored["clean"] = False
    freezer.tick(timedelta(days=2))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    tracker = hass.states.get("device_tracker.phone")
    # The phone may have left after that write: a new visit, not one spanning the outage.
    assert tracker.state == "home"
    assert tracker.attributes["arrived_at"] != arrived_at
    assert tracker.attributes["arrived_at"] == dt_util.utcnow().isoformat()


async def refresh_after(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, coordinator: Any, seconds: int
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_total_failure_tolerated_within_grace(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    # Default grace period: 180 s after the last successful poll.
    entry = await setup_entry(hass, ap("ap1"), PHONE_SUB)
    coordinator = entry.runtime_data
    FakeDriver.results["ap1"] = AccessPointError("down")
    for n in (1, 2):  # 61 s and 122 s after the last good poll: still within grace
        await refresh_after(hass, freezer, coordinator, 61)
        assert coordinator.last_update_success, n
        assert hass.states.get("device_tracker.phone").state == "home", n
        assert hass.states.get("sensor.hallway_ap_clients").state == "unavailable", n
    await refresh_after(hass, freezer, coordinator, 61)  # 183 s: past the grace period
    assert not coordinator.last_update_success
    assert hass.states.get("device_tracker.phone").state == "unavailable"
    FakeDriver.results["ap1"] = GOOD
    await refresh_after(hass, freezer, coordinator, 61)
    assert hass.states.get("device_tracker.phone").state == "home"
    assert hass.states.get("sensor.hallway_ap_clients").state == "1"


async def test_total_failure_past_short_grace_is_unavailable_not_away(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    # With a 30 s grace period the next poll is already past it: the outage must make the
    # trackers unavailable, not report everyone as having left.
    entry = await setup_entry(hass, ap("ap1"), PHONE_SUB, options={"consider_home": 30})
    coordinator = entry.runtime_data
    FakeDriver.results["ap1"] = AccessPointError("down")
    await refresh_after(hass, freezer, coordinator, 61)
    assert not coordinator.last_update_success
    assert hass.states.get("device_tracker.phone").state == "unavailable"


async def test_first_refresh_failure_retries_setup(hass: HomeAssistant) -> None:
    FakeDriver.results["ap1"] = AccessPointError("down")
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=[ap("ap1")])
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_invalid_setting_skips_only_that_ap(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, ap("ap1"), ap("bad", "Broken"))
    assert entry.state is ConfigEntryState.LOADED
    assert [a.title for a in entry.runtime_data.access_points] == ["Hallway AP"]
    assert await _async_build_and_test(hass, FakeDriver, {"host": "bad"}) == ({"host": "invalid_value"}, None)


async def test_area_sensor_follows_only_access_point_devices(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, ap("ap1"), PHONE_SUB)
    registry = dr.async_get(hass)
    kitchen = ar.async_get(hass).async_get_or_create("Kitchen")
    ap_device = registry.async_get_device_by_identifier(
        (DOMAIN, entry.runtime_data.access_points[0].subentry_id), entry.entry_id
    )
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    unrelated = registry.async_get_or_create(config_entry_id=other.entry_id, identifiers={("other", "1")})
    target = "custom_components.wifi_association_presence.sensor.TrackedDeviceSensor.async_write_ha_state"
    with patch(target) as write:
        registry.async_update_device(unrelated.id, sw_version="2")
        registry.async_update_device(unrelated.id, area_id=kitchen.id)
        registry.async_update_device(ap_device.id, sw_version="2")
        await hass.async_block_till_done()
        assert write.call_count == 0
    registry.async_update_device(ap_device.id, area_id=kitchen.id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.phone_area").state == "Kitchen"
    assert hass.states.get("sensor.phone_area").attributes["area_id"] == kitchen.id
