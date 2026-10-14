# Design note: cars by Bluetooth, from Tesla BLE ESP32s

Status: **planned**, nothing built. Written 2026-10-14.

Many homes have **one access point** for the whole house: Wi-Fi then says "home", never "where". A Tesla owner running [esphome-tesla-ble-multi](https://github.com/darek-margas/esphome-tesla-ble-multi) may have an ESP32 in the garage and another by the driveway. Each knows which cars are near it, by Bluetooth. Used as a presence source here, they give the car a real location, and the existing presence logic (grace period, visits, areas) applies unchanged.

| Source | What it knows about the car |
|---|---|
| Access point (one for the house) | Home or away, if the car joins the Wi-Fi. No useful area. |
| ESP32 in the garage | In the garage |
| ESP32 by the driveway | On the driveway |

## The source

- A new access point type, **Tesla BLE (ESPHome)**. Each ESP32 running the Tesla component counts as one access point.
- It reads **Home Assistant entities**, as the UniFi driver reads through the UniFi Network integration: no login to the ESP32 and no extra connection. Per car on that ESP32 it uses:
  - **Present** (binary sensor): the car is a client of this "access point";
  - **BLE Advert RSSI** (dBm): the signal;
  - **BLE MAC** (text sensor): the client's MAC, so a car fits the integration's MAC-keyed model like any Wi-Fi client;
  - **Car ID** (the `S<16 hex>C` advert name derived from the VIN): only to tell which ESP32 entities belong to the same car.
- **Area:** the Home Assistant area of the ESP32's device, as for any access point.
- **Its own grace period stays.** The ESP32's `presence_timeout` already smooths *Present* across turn-taking. Our grace period applies on top, as for a phone that drops off Wi-Fi briefly.
- It lives in the **integration**, not in the wifi-ap-associations library, because it reads Home Assistant state. The library stays free of Home Assistant.

## One tracked car, two MACs

- A car can be seen by its **Wi-Fi MAC** (when it joins the home Wi-Fi) and by its **Bluetooth MAC** (from the ESP32s). Today a tracked device has exactly one MAC.
- **Change:** a tracked device gets a **list of MACs**. Any of them seen counts as the device being seen. Everything else (storage, picker, merge) stays MAC-based.
- When adding a tracked device, the picker lists cars from Tesla BLE sources by name (from the ESP32), next to Wi-Fi clients. A car that was never found has no BLE MAC yet, and no ESP32 sees it anyway.
- Stored identity stays backwards compatible: existing tracked devices keep their single MAC.
- Tesla BLE Fleet matches cars across ESP32s by **Car ID** instead, because it must work before a car is found and must not split on a mistyped YAML MAC. Here the MAC is enough.

## Which sighting wins the area

- Today the strongest signal (quality 0–100) wins across access points.
- Wi-Fi and Bluetooth signal levels aren't comparable, so: **a Bluetooth sighting wins the area while there is one**, because it's the local, finer one. Wi-Fi decides home/away and the area only when no Bluetooth source sees the car.
- Between two Bluetooth sources, the stronger BLE Advert RSSI wins, with a few dB of hysteresis so the area doesn't flip between two ESP32s that hear the car equally.

## Why here and not in Tesla BLE Fleet

[Tesla BLE Fleet](https://github.com/darek-margas/tesla-ble-fleet) (one device per car across several ESP32s) also needs presence. Building it twice would duplicate this integration's presence logic: visits, grace period, areas, storage across restarts. So presence lives here, and the fleet keeps commands, mirrored sensors, the combined media player and keys. Each project points to the other.

## Before building

- **Needs:** the component's **BLE MAC** sensor (exists), and **Car ID** to group one car's entities on an ESP32 (esphome-tesla-ble-multi `multicar-v2`, commit a1e548a).
- **Order:**
  1. tracked devices with several MACs (tests first: storage, migration, merge);
  2. the Tesla BLE source (reading entities, the area of the ESP32);
  3. the "Bluetooth wins the area" rule.
- **Meanwhile, without code:** a Tesla on the home Wi-Fi can already be tracked by its Wi-Fi MAC. A README note can say so.
