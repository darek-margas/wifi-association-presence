# Design note: parking spots

Status: **released in 0.13.0** (betas b1–b3); presence sensors from b2. Written 2026-11-23.

A car on the home Wi-Fi can be tracked by its Wi-Fi MAC like a phone, until it goes to sleep. On 23 November one of our Teslas was parked in the garage, and Tesla BLE Fleet had it in its spot. Its tracker here had been `not_home` since 12:23, when it went to sleep and dropped off the Wi-Fi.

The [Bluetooth source](tesla-ble-source.md) planned for Tesla BLE ESP32s will keep a sleeping car home, because the car keeps advertising for the phone key. It still can't say whether the car is in the garage or on the driveway. An ESP32 by the garage door hears it about as well either way.

## A spot

A **parking spot** is a config subentry:
- an **occupancy** sensor over one space (for example an ultrasonic sensor in the ceiling);
- optionally its **distance** sensor;
- the **floor distance**, the car height band, and two RSSI thresholds;
- for each car that uses parking spots, that car's **RSSI measured at the spot**, typically an ESPHome `ble_rssi` sensor for the car's Bluetooth MAC on the ESP32 above it.

Any number of spots can be added. Each is evaluated on its own whenever one of its sensors changes, not only on the minute poll.

Two questions are answered separately:
- **Is it a car?** Height = floor distance − measured distance. Outside the band (default 135–195 cm) it is a ladder, a bin or a person.
- **Which car?** The strongest advert at the spot, if above the minimum (default −85 dBm) and at least the margin (default 6 dB) ahead of the next car. Closer than that, the spot waits.

Once a car is identified, the spot keeps it until it is empty. A parked car doesn't change, and RSSI wobbles: the on-device "Parked Car" sensor of the first build flipped between a name and Unknown every few seconds. An open tailgate or a weak advert doesn't move the car out either.

With only one car using parking spots, no RSSI is needed: anything car-shaped is that car, including a guest's car. Give the car an RSSI to tell them apart.

## Which car is a parking car

A tracked device gets an option, **Car: placed by parking spots**, when it is added or reconfigured. Only devices with it on get a Parking sensor and an RSSI field in the spot form. Devices saved before 0.13 have it off.

## The spot wins

A spot that has identified a car outranks every other source. The car is **home**, in the **spot's area**, whatever the access points (or, later, Bluetooth) say. The order of evidence for a car's place is:
1. a parking spot;
2. its presence sensor (below), until the Bluetooth source replaces it;
3. Wi-Fi.

The spot is the only one that knows the car is under it. Both radio sources only know it is near.

What changes for a car in a spot:
- the **tracker** is home, its `access_point` is the spot's name, and its `area` is the area of the spot's device. The spot device starts in the occupancy sensor's area, and can be moved;
- `arrived_at` is when the Wi-Fi saw the visit start if the car was already home by Wi-Fi, otherwise when the spot identified it. So it doesn't move when the car later drops off the Wi-Fi;
- `departed_at`, once it is away, is the later of its last Wi-Fi sighting and when it left the spot;
- a `parking_spot` attribute (the spot's name, or none) on parking cars' trackers only;
- the **Access point** and **Area** sensors show the spot and its area.

New entities:
- per parking car, **Parking**: `in_spot` (attribute `spot`), `outside` (home by Wi-Fi but in no spot), `away`, or `home` when a spot can't tell (its sensor is unavailable, or two cars are too close in RSSI);
- per spot, **Occupant**: the car's name, `empty`, `other` (wrong height, or no car heard near it), or `unsure`.

## Sensors that stop reporting (0.14.0b3)

b1 to 0.14.0b2 treated an unavailable sensor as "the car isn't there". ESPHome devices are unavailable at every Home Assistant restart until they connect, and whenever the ESP32 restarts, so a sleeping car turned away and arrived again each time, and arrival automations fired.

Now a spot that held a car, or a presence sensor that was on, keeps its answer for the **grace period** after it stops reporting, the same rule as for a device whose access point can't be read. Past it, the spot is unknown and the presence sensor says nothing, and `departed_at` is the moment it stopped reporting. The latch itself survives: a spot that reports occupied again still has its car.

The car in each spot, when its visit started and when it left are saved with the sightings (every 10 minutes, at stop and at unload). The presence sensors' visit times are kept by the integration and saved too, because Home Assistant's `last_changed` starts again at every restart.

## Presence sensors

The first day of b1 showed the gap at once: Szarik was asleep on the charger on the driveway, off the Wi-Fi and in no spot, so it was away. The Tesla BLE ESP32 by the street still had it Present.

A tracked device can name a **presence sensor**: any binary sensor that is on while the device is near. While it is on, the device is home in that sensor's area (the entity's, else its device's, else that of the device it is attached to), with the name of the device that hears it as `access_point`: for a car sub-device of an esphome-tesla-ble-multi ESP32, the ESP32; for a car in no spot that is `outside`. When it turns off, Wi-Fi decides again, and `departed_at` counts the moment it turned off. Unavailable counts as no evidence.

It is the smallest step toward the [Bluetooth source](tesla-ble-source.md): one entity per device instead of every ESP32 as an access point, no RSSI comparison between ESP32s, and the ESP32's own `presence_timeout` as the only smoothing. It is generic, so it works for anything with a presence binary sensor.

## What stays in Tesla BLE Fleet

Where the car is lives here. What to do about it stays in [Tesla BLE Fleet](https://github.com/darek-margas/tesla-ble-fleet): its **Tesla outside** sensor answers which car to honk when Frigate sees the driveway blocked, and the horn is a fleet command. So this integration has no fleet-wide summary or "outside" binary sensor.

The fleet keeps its own parking too, for owners who don't run this integration. Its Present comes straight from the ESP32s, with no grace period. The logic is one file, `parking.py`, the same in both repositories: change both, and the tests in each (`tests/test_parking.py`) are the same.

Use one of them per spot, not both. With both, a car gets two Parking/Location sensors that disagree for a few minutes whenever the car leaves: the fleet's turns away when Present drops, this one after the grace period.

## Limits

- **Outside or away** comes from the presence sensor, else the access points. Without a presence sensor, a sleeping Tesla on the driveway, off the Wi-Fi and in no spot, is away.
- **A restart after more than an hour starts afresh.** The car in each spot is saved with the sightings and taken up again at a restart or reload, but not if it was saved over an hour ago: the cars may have been swapped meanwhile. The spot then identifies its car again from the next readings.
- **One spot unsure or unavailable** makes every car that isn't in a spot "home, place unclear", because it might be in that spot.
- A car identified in two spots at once (two spots both hearing it strongest) isn't expected and isn't handled.

## Hardware we use

[examples/esphome/parking-spot.yaml](../../examples/esphome/parking-spot.yaml) has the full ESPHome config:
- an ESP32-C3 Super Mini and an HC-SR04 in the ceiling, 280 cm above the floor, with a 10k/20k divider on the echo pin;
- one `ble_rssi` sensor per car;
- Bluetooth scanning cut to about 9 % of the radio's time, so the C3's Wi-Fi keeps working.

The box is polle87's [Universal Distance Sensor for Home Assistant](https://makerworld.com/en/models/3368970) on MakerWorld, which also lists the parts.
