"""Parking spots in Home Assistant: a spot places the cars, and outranks the Wi-Fi."""

from __future__ import annotations

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

from test_core_ha import PHONE, PHONE_SUB, FakeDriver, ap, fake_driver  # noqa: F401

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
    assert diag["spots"]["Garage"]["kind"] == "car"
    assert diag["spots"]["Garage"]["car"] == "Bluey"
    assert diag["cars"]["Szarik"] == {"location": "away", "spot": None}

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
