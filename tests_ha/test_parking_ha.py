"""Parking spots in Home Assistant: a spot places the cars, and outranks the Wi-Fi."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from wifi_ap_associations import AccessPointInfo, AssociatedClient, PollResult

from custom_components.wifi_association_presence.const import DOMAIN
from custom_components.wifi_association_presence.diagnostics import (
    async_get_config_entry_diagnostics,
)

from test_core_ha import (  # noqa: F401
    PHONE, PHONE_SUB, FakeDriver, ap, fake_driver, refresh_after,
)

BLUEY = "0C:29:8F:00:00:01"
SZARIK = "0C:29:8F:00:00:02"
OCCUPIED = "binary_sensor.garage_parking_1_car_present"
DISTANCE = "sensor.garage_parking_1_distance"
BLUEY_RSSI = "sensor.garage_parking_1_bluey_rssi"
SZARIK_RSSI = "sensor.garage_parking_1_szarik_rssi"
YL_ROOF = "113"  # a Model Y L under a sensor 280 cm above the floor


def car(mac: str, title: str, sub_id: str) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type="tracked_device", title=title, unique_id=mac, subentry_id=sub_id,
        data={"mac": mac, "parking": True},
    )


def spot(rssi: dict[str, str] | None = None, **extra) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type="parking_spot", title="Garage", unique_id=None, subentry_id="spot1",
        data={
            "name": "Garage", "occupied": OCCUPIED, "distance": DISTANCE, "floor_cm": 280,
            "min_height_cm": 135, "max_height_cm": 195, "rssi_min": -85, "rssi_margin": 6,
            "rssi": {"bluey": BLUEY_RSSI, "szarik": SZARIK_RSSI} if rssi is None else rssi,
            **extra,
        },
    )


def sensors(hass: HomeAssistant, occupied: str, distance: str, bluey: str, szarik: str) -> None:
    # Occupancy last, as on the ESP32 (it turns on 5 s after the distance drops): a
    # spot decides when it turns occupied, on the readings it has then.
    hass.states.async_set(BLUEY_RSSI, bluey, {"unit_of_measurement": "dBm"})
    hass.states.async_set(SZARIK_RSSI, szarik, {"unit_of_measurement": "dBm"})
    hass.states.async_set(DISTANCE, distance, {"unit_of_measurement": "cm"})
    hass.states.async_set(OCCUPIED, occupied)


async def setup(hass: HomeAssistant, *subentries: ConfigSubentryData) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Wi-Fi", data={}, subentries_data=list(subentries))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def wifi(*macs: str) -> PollResult:
    return PollResult(
        [AssociatedClient(mac, "IOT", "5GHz", 80) for mac in macs],
        AccessPointInfo(name="Hallway AP"),
    )


CARS = (car(BLUEY, "Bluey", "bluey"), car(SZARIK, "Szarik", "szarik"))


async def test_spot_wins_over_wifi(hass: HomeAssistant) -> None:
    # Bluey asleep in the garage, off the Wi-Fi: the spot has it home, in the spot's area.
    FakeDriver.results = {"ap1": wifi(PHONE)}
    sensors(hass, "on", YL_ROOF, "-60", "-90")
    entry = await setup(hass, ap("ap1"), PHONE_SUB, *CARS, spot())

    tracker = hass.states.get("device_tracker.bluey")
    assert tracker.state == "home"
    assert tracker.attributes["parking_spot"] == "Garage"
    assert tracker.attributes["access_point"] == "Garage"
    assert hass.states.get("sensor.bluey_access_point").state == "Garage"
    assert tracker.attributes["arrived_at"] is not None
    assert tracker.attributes["departed_at"] is None
    assert hass.states.get("sensor.bluey_parking").state == "in_spot"
    assert hass.states.get("sensor.bluey_parking").attributes["spot"] == "Garage"
    assert hass.states.get("sensor.szarik_parking").state == "away"
    assert hass.states.get("device_tracker.szarik").state == "not_home"
    assert hass.states.get("sensor.garage_occupant").state == "Bluey"
    # A phone has no parking attribute or sensor.
    assert "parking_spot" not in (hass.states.get("device_tracker.phone").attributes)
    assert hass.states.get("sensor.phone_parking") is None

    diag = (await async_get_config_entry_diagnostics(hass, entry))["parking"]
    assert [(s["name"], s["kind"], s["car"]) for s in diag["spots"]] == [("Garage", "car", "Bluey")]
    assert {"name": "Szarik", "location": "away", "spot": None} in diag["cars"]
    assert {"name": "Bluey", "location": "in_spot", "spot": "Garage"} in diag["cars"]

    # The spot's device takes an area: the car's Area sensor follows.
    garage = ar.async_get(hass).async_create("Garage")
    devices = dr.async_get(hass)
    spot_device = devices.async_get_device_by_identifier((DOMAIN, "spot1"), entry.entry_id)
    devices.async_update_device(spot_device.id, area_id=garage.id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bluey_area").state == "Garage"
    assert hass.states.get("sensor.bluey_area").attributes["area_id"] == garage.id

    # Bluey drives out: the spot empties, and with no Wi-Fi it is away at once.
    sensors(hass, "off", "280", "-70", "-90")
    await hass.async_block_till_done()
    tracker = hass.states.get("device_tracker.bluey")
    assert tracker.state == "not_home"
    assert tracker.attributes["parking_spot"] is None
    assert tracker.attributes["departed_at"] is not None
    assert hass.states.get("sensor.bluey_parking").state == "away"
    assert hass.states.get("sensor.bluey_access_point").state == "unknown"
    assert hass.states.get("sensor.garage_occupant").state == "empty"


async def test_on_wifi_but_in_no_spot_is_outside(hass: HomeAssistant) -> None:
    FakeDriver.results = {"ap1": wifi(BLUEY, SZARIK)}
    sensors(hass, "on", YL_ROOF, "-88", "-58")
    await setup(hass, ap("ap1"), *CARS, spot())
    assert hass.states.get("sensor.szarik_parking").state == "in_spot"
    assert hass.states.get("sensor.bluey_parking").state == "outside"
    assert hass.states.get("device_tracker.bluey").state == "home"


async def test_ladder_and_unsure(hass: HomeAssistant) -> None:
    FakeDriver.results = {"ap1": wifi(BLUEY, SZARIK)}
    sensors(hass, "on", "70", "-60", "-90")  # 210 cm tall: not a car
    await setup(hass, ap("ap1"), *CARS, spot())
    assert hass.states.get("sensor.garage_occupant").state == "other"
    assert hass.states.get("sensor.bluey_parking").state == "outside"

    sensors(hass, "off", "280", "-60", "-90")
    await hass.async_block_till_done()
    sensors(hass, "on", YL_ROOF, "-70", "-72")  # too close to tell
    await hass.async_block_till_done()
    assert hass.states.get("sensor.garage_occupant").state == "unsure"
    assert hass.states.get("sensor.bluey_parking").state == "home"


async def test_one_car_without_rssi(hass: HomeAssistant) -> None:
    # A single car using parking spots: height alone identifies it.
    FakeDriver.results = {"ap1": wifi()}
    sensors(hass, "on", YL_ROOF, "unknown", "unknown")
    await setup(hass, ap("ap1"), car(BLUEY, "Bluey", "bluey"), spot(rssi={}))
    assert hass.states.get("sensor.garage_occupant").state == "Bluey"
    assert hass.states.get("device_tracker.bluey").state == "home"


async def test_spot_flow_and_parking_option(hass: HomeAssistant) -> None:
    FakeDriver.results = {"ap1": wifi()}
    entry = await setup(hass, ap("ap1"), PHONE_SUB)
    flows = hass.config_entries.subentries

    # No car uses parking spots yet.
    result = await flows.async_init((entry.entry_id, "parking_spot"), context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_parking_cars"

    # Make the phone a car (an odd car, but any tracked device can be one).
    phone = next(s for s in entry.subentries.values() if s.subentry_type == "tracked_device")
    result = await entry.start_subentry_reconfigure_flow(hass, phone.subentry_id)
    result = await flows.async_configure(result["flow_id"], {"name": "Phone", "parking": True})
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries[phone.subentry_id].data["parking"] is True
    assert hass.states.get("sensor.phone_parking") is not None

    sensors(hass, "on", YL_ROOF, "-60", "-90")
    result = await flows.async_init((entry.entry_id, "parking_spot"), context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    assert "Phone RSSI" in result["data_schema"].schema
    result = await flows.async_configure(
        result["flow_id"],
        {"name": " Garage ", "occupied": OCCUPIED, "distance": DISTANCE, "floor_cm": 280,
         "min_height_cm": 135, "max_height_cm": 195, "rssi_min": -85, "rssi_margin": 6,
         "Phone RSSI": BLUEY_RSSI},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sub = next(s for s in entry.subentries.values() if s.subentry_type == "parking_spot")
    assert sub.title == "Garage"
    assert sub.data["rssi"] == {phone.subentry_id: BLUEY_RSSI}
    assert hass.states.get("sensor.garage_occupant").state == "Phone"
    assert hass.states.get("device_tracker.phone").state == "home"

    # Parking off again: its Parking sensor goes.
    result = await entry.start_subentry_reconfigure_flow(hass, phone.subentry_id)
    result = await flows.async_configure(result["flow_id"], {"name": "Phone", "parking": False})
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{PHONE}_parking") is None


async def test_presence_sensor_keeps_a_sleeping_car_home(hass: HomeAssistant) -> None:
    # Szarik asleep on the driveway: off the Wi-Fi, in no spot, but the Tesla BLE
    # ESP32 by the street still hears it (Present on).
    present = "binary_sensor.szarik_present"
    hass.states.async_set(present, "on")
    FakeDriver.results = {"ap1": wifi()}
    sensors(hass, "on", YL_ROOF, "-60", "-93")
    szarik = car(SZARIK, "Szarik", "szarik")
    szarik["data"] = {**szarik["data"], "presence_sensor": present}
    await setup(hass, ap("ap1"), car(BLUEY, "Bluey", "bluey"), szarik, spot())

    tracker = hass.states.get("device_tracker.szarik")
    assert tracker.state == "home"
    assert tracker.attributes["arrived_at"] is not None
    assert hass.states.get("sensor.szarik_parking").state == "outside"
    assert hass.states.get("sensor.bluey_parking").state == "in_spot"

    # Szarik drives off: Present turns off, and with no Wi-Fi it is away at once.
    hass.states.async_set(present, "off")
    await hass.async_block_till_done()
    tracker = hass.states.get("device_tracker.szarik")
    assert tracker.state == "not_home"
    assert tracker.attributes["departed_at"] is not None
    assert hass.states.get("sensor.szarik_parking").state == "away"
    assert hass.states.get("sensor.szarik_access_point").state == "unknown"

    # Back, and into the spot: the spot wins over the sensor.
    hass.states.async_set(present, "on")
    sensors(hass, "off", "280", "-60", "-93")
    sensors(hass, "on", YL_ROOF, "-90", "-55")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.szarik_parking").state == "in_spot"
    assert hass.states.get("sensor.szarik_access_point").state == "Garage"


async def test_presence_sensor_on_a_sub_device(hass: HomeAssistant) -> None:
    # esphome-tesla-ble-multi: Present is on the car's sub-device ("Szarik", no area),
    # attached to the ESP32 ("Tesla BLE", in the garage). The ESP32 is what hears it.
    other = MockConfigEntry(domain="esphome")
    other.add_to_hass(hass)
    garage = ar.async_get(hass).async_create("Garage")
    devices = dr.async_get(hass)
    esp32 = devices.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("esphome", "esp32")}, name="Tesla BLE"
    )
    devices.async_update_device(esp32.id, area_id=garage.id)
    sub = devices.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("esphome", "esp32_szarik")},
        name="Szarik", via_device_id=esp32.id,
    )
    entity = er.async_get(hass).async_get_or_create(
        "binary_sensor", "esphome", "szarik_present", device_id=sub.id,
        suggested_object_id="szarik_present",
    )
    hass.states.async_set(entity.entity_id, "on")
    FakeDriver.results = {"ap1": wifi()}
    szarik = car(SZARIK, "Szarik", "szarik")
    szarik["data"] = {**szarik["data"], "presence_sensor": entity.entity_id}
    await setup(hass, ap("ap1"), szarik)

    assert hass.states.get("device_tracker.szarik").state == "home"
    assert hass.states.get("sensor.szarik_access_point").state == "Tesla BLE"
    assert hass.states.get("sensor.szarik_area").state == "Garage"
    assert hass.states.get("device_tracker.szarik").attributes["area"] == "Garage"


PRESENT = "binary_sensor.szarik_present"


def szarik_with_sensor() -> ConfigSubentryData:
    sub = car(SZARIK, "Szarik", "szarik")
    sub["data"] = {**sub["data"], "presence_sensor": PRESENT}
    return sub


async def test_spot_sensor_not_reporting_is_ridden_out(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    # The spot's ESP32 restarts: unavailable is no evidence that the car left.
    FakeDriver.results = {"ap1": wifi()}
    sensors(hass, "on", YL_ROOF, "-60", "-90")
    entry = await setup(hass, ap("ap1"), *CARS, spot())
    arrived = hass.states.get("device_tracker.bluey").attributes["arrived_at"]

    hass.states.async_set(OCCUPIED, "unavailable")
    await hass.async_block_till_done()
    await refresh_after(hass, freezer, entry.runtime_data, 61)
    tracker = hass.states.get("device_tracker.bluey")
    assert (tracker.state, tracker.attributes["arrived_at"]) == ("home", arrived)
    assert hass.states.get("sensor.bluey_parking").state == "in_spot"
    assert hass.states.get("sensor.garage_occupant").state == "Bluey"

    hass.states.async_set(OCCUPIED, "on")
    await hass.async_block_till_done()
    tracker = hass.states.get("device_tracker.bluey")
    assert (tracker.state, tracker.attributes["arrived_at"]) == ("home", arrived)

    # Not reporting for longer than the grace period (180 s): the spot no longer knows.
    hass.states.async_set(OCCUPIED, "unavailable")
    await hass.async_block_till_done()
    for _ in range(4):
        await refresh_after(hass, freezer, entry.runtime_data, 61)
    assert hass.states.get("device_tracker.bluey").state == "not_home"
    assert hass.states.get("sensor.garage_occupant").state == "unknown"
    # Reporting again, still occupied: the same car (the spot kept its latch).
    hass.states.async_set(OCCUPIED, "on")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.garage_occupant").state == "Bluey"


async def test_presence_sensor_not_reporting_is_ridden_out(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    FakeDriver.results = {"ap1": wifi()}
    hass.states.async_set(PRESENT, "on")
    entry = await setup(hass, ap("ap1"), szarik_with_sensor())
    arrived = hass.states.get("device_tracker.szarik").attributes["arrived_at"]

    freezer.tick(timedelta(seconds=30))
    hass.states.async_set(PRESENT, "unavailable")
    await hass.async_block_till_done()
    silent_at = hass.states.get(PRESENT).last_changed
    tracker = hass.states.get("device_tracker.szarik")
    assert (tracker.state, tracker.attributes["arrived_at"]) == ("home", arrived)

    await refresh_after(hass, freezer, entry.runtime_data, 61)
    hass.states.async_set(PRESENT, "on")
    await hass.async_block_till_done()
    tracker = hass.states.get("device_tracker.szarik")
    assert (tracker.state, tracker.attributes["arrived_at"]) == ("home", arrived)

    # Silent past the grace period: away, since the moment it stopped reporting.
    hass.states.async_set(PRESENT, "unavailable")
    await hass.async_block_till_done()
    silent_at = hass.states.get(PRESENT).last_changed
    for _ in range(4):
        await refresh_after(hass, freezer, entry.runtime_data, 61)
    tracker = hass.states.get("device_tracker.szarik")
    assert tracker.state == "not_home"
    assert tracker.attributes["departed_at"] == silent_at.isoformat()


async def test_restart_keeps_parked_and_present_cars(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    # Home Assistant restarts: the ESP32s aren't connected yet at the first poll.
    FakeDriver.results = {"ap1": wifi()}
    hass.states.async_set(PRESENT, "on")
    sensors(hass, "on", YL_ROOF, "-60", "-93")
    entry = await setup(hass, ap("ap1"), car(BLUEY, "Bluey", "bluey"), szarik_with_sensor(), spot())
    arrived = {
        name: hass.states.get(f"device_tracker.{name}").attributes["arrived_at"]
        for name in ("bluey", "szarik")
    }
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(timedelta(minutes=5))
    sensors(hass, "unavailable", "unavailable", "unavailable", "unavailable")
    hass.states.async_set(PRESENT, "unavailable")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for name in ("bluey", "szarik"):
        tracker = hass.states.get(f"device_tracker.{name}")
        assert (tracker.state, tracker.attributes["arrived_at"]) == ("home", arrived[name]), name
    assert hass.states.get("sensor.bluey_parking").state == "in_spot"
    assert hass.states.get("sensor.szarik_parking").state == "outside"

    # The ESP32s connect: nothing changes.
    sensors(hass, "on", YL_ROOF, "-60", "-93")
    hass.states.async_set(PRESENT, "on")
    await hass.async_block_till_done()
    for name in ("bluey", "szarik"):
        tracker = hass.states.get(f"device_tracker.{name}")
        assert (tracker.state, tracker.attributes["arrived_at"]) == ("home", arrived[name]), name


async def test_restart_then_spot_is_empty(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    # The car left while Home Assistant was down: the saved latch goes with the first
    # "empty" report.
    FakeDriver.results = {"ap1": wifi()}
    sensors(hass, "on", YL_ROOF, "-60", "-93")
    entry = await setup(hass, ap("ap1"), *CARS, spot())
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(timedelta(minutes=5))
    sensors(hass, "off", "280", "unknown", "unknown")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.bluey").state == "not_home"
    assert hass.states.get("sensor.garage_occupant").state == "empty"


async def test_long_downtime_starts_afresh(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    # Saved over an hour ago: too much may have happened; nothing is held.
    FakeDriver.results = {"ap1": wifi()}
    sensors(hass, "on", YL_ROOF, "-60", "-93")
    entry = await setup(hass, ap("ap1"), *CARS, spot())
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(timedelta(hours=2))
    sensors(hass, "unavailable", "unavailable", "unavailable", "unavailable")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.bluey").state == "not_home"


async def test_spot_form_rejects_an_impossible_height_band(hass: HomeAssistant) -> None:
    FakeDriver.results = {"ap1": wifi()}
    entry = await setup(hass, ap("ap1"), car(BLUEY, "Bluey", "bluey"))
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, "parking_spot"), context={"source": "user"})
    entered = {"name": "Garage", "occupied": OCCUPIED, "floor_cm": 280, "min_height_cm": 195,
               "max_height_cm": 135, "rssi_min": -85, "rssi_margin": 6}
    result = await flows.async_configure(result["flow_id"], entered)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"max_height_cm": "height_band"}
    result = await flows.async_configure(
        result["flow_id"], {**entered, "min_height_cm": 285, "max_height_cm": 295}
    )
    assert result["errors"] == {"min_height_cm": "height_above_floor"}
    result = await flows.async_configure(
        result["flow_id"], {**entered, "min_height_cm": 135, "max_height_cm": 195}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
