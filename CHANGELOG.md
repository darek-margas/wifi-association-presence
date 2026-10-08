# Changelog

## 0.5.0 — OpenWrt and UniFi access points

Everything from the 0.5.0 betas (b1 to b4, below), for those upgrading from 0.4.2.

### Features
- New access point type **OpenWrt (SSH, ubus)**: reads every radio's hostapd over ubus
  (associated and authorized stations, band, signal in dBm, SSID) and the AP's name,
  model, firmware, uptime and memory, with one SSH command per poll. Verified end to end
  in Home Assistant on OpenWrt 25.12.5 and on 22.03.7 (older `hostapd.wlan0` naming), in
  VMs with simulated radios; reports from physical routers are welcome.
- New access point type **UniFi (via the UniFi Network integration)**, **experimental**
  until a UniFi user confirms it on a real controller: pick a UniFi AP known to Home
  Assistant's UniFi Network integration; its clients are read from the controller's
  active client list over that integration's connection, with no separate login and one
  request per poll for all APs of a controller. The AP keeps its UniFi device, whose
  area is what the tracked devices report.
- Drivers can be marked experimental (shown so in the type list), and declare which
  device details they report, so only those sensors are created (OpenWrt APs get no
  Location or CPU sensor).

### Changes
- Sightings and visit start times survive a Home Assistant restart: the file is written
  when Home Assistant stops and records when, so a device within the grace period then
  continues its visit. Otherwise it is written at most every 10 minutes, not every poll.
  (Thanks to David Coulson.)
- A poll in which no access point at all can be read is ridden out on the previous
  sightings within the grace period, instead of making every entity unavailable at once.
- Access point CPU and memory sensors are disabled by default (a recorder row per AP per
  minute otherwise).
- An access point whose settings the driver rejects is skipped with an error in the log
  instead of failing the whole integration.
- The tracked devices' Area sensors only react to area changes of the access points'
  devices, not to every device registry update.

### Collector (`scripts/collect.py`)
- Profiles `openwrt` and `cisco_wlc` (Catalyst 9800), SNMPv3 SHA-2 / AES-256, and wider
  redaction (Cisco-style and bare MACs, IPv6 addresses, Cisco serial numbers).

## 0.5.0b4 — OpenWrt verified, tidier access point sensors (beta)

Beta: in HACS, enable "Show beta versions" for this integration to install it.

### Changes
- **OpenWrt is no longer experimental.** Verified on OpenWrt 25.12.5 end to end in Home
  Assistant: the access point device and its sensors, tracked clients with SSID, band
  and signal, areas, and departure / arrival times. Tested in a VM with simulated radios
  (the guide is in `docs/openwrt-vm-test.md`), and also on OpenWrt 22.03.7 with its older
  `hostapd.wlan0` naming; reports from physical routers are welcome.
- Access point sensors are only created for details the driver can report: OpenWrt APs no
  longer get a Location sensor (OpenWrt has no such setting) or a CPU sensor (it reports
  load averages, not a percentage). Such sensors left by earlier versions are removed.
  Drivers declare this in `REPORTS`; D-Link is unchanged.
- The OpenWrt test guide covers VMware, ESXi, QNAP and others, and the traps found on
  25.12: protect your LAN first, reboot after installing Wi-Fi, set a country code.

## 0.5.0b3 — OpenWrt access points (beta)

Beta: in HACS, enable "Show beta versions" for this integration to install it.

### Features
- New access point type **OpenWrt (SSH, ubus)**, marked experimental: reads every radio's
  hostapd over ubus (associated and authorized stations, band, signal in dBm, SSID) and
  the AP's name, model, firmware, uptime and memory, with one SSH command per poll.
  Written from OpenWrt's source and verified on OpenWrt 25.12.5 in a VM with simulated
  radios (not yet on a physical router). Full data from OpenWrt 21.02; clients without
  signal and SSID on 18.06 / 19.07.
- Drivers can be marked experimental (shown so in the type list) until confirmed on
  hardware, so untested drivers can ship without affecting the others.

### Collector (`scripts/collect.py`)
- Profiles `openwrt` and `cisco_wlc` (Catalyst 9800); on Cisco, SNMP also walks the
  Airespace and LWAPP client and AP tables.
- SNMPv3 `--snmp-auth` (md5 to sha512) and `--snmp-priv` (des, 3des, aes, aes192, aes256,
  aes192c / aes256c).
- Also redacts Cisco-style and bare MACs, IPv6 addresses and Cisco serial numbers; table
  rows are no longer cut off by the secret-setting rule.

## 0.5.0b2 — restart fix (beta)

Beta: in HACS, enable "Show beta versions" for this integration to install it.

### Fixes
- Sightings and visit start times survive a Home Assistant restart. Home Assistant does
  not unload integrations when it stops, so the save on unload never ran then, and in
  0.5.0b1 there was no pending delayed write for Home Assistant to flush either: a
  restart lost up to 10 minutes of sightings and reset `arrived_at` to the boot time.
  The file is now also written when Home Assistant stops, and it records when it was
  written, so a device that was still within the grace period at that moment continues
  its visit after a restart however long the restart took. (Thanks to David Coulson.)

## 0.5.0b1 — UniFi access points (beta)

Beta: in HACS, enable "Show beta versions" for this integration to install it.
Feedback, especially from UniFi users, is welcome in the issues.

### Features
- New access point type **UniFi (via the UniFi Network integration)**: pick a UniFi AP
  known to Home Assistant's UniFi Network integration; its associated clients are read
  from the controller's active client list over that integration's connection, with no
  separate login. One request per poll serves all the APs of a controller, and a failed
  one is remembered for the same 20 s so a down controller gets one attempt per poll.
  The AP gets no device or sensors of its own: the area of its UniFi device is what the
  tracked devices report, and UniFi already has its client count, uptime, CPU and memory.
- Driver hook `SOURCE` for drivers that read through another Home Assistant integration:
  the data source and the list to pick the access point from come from a registry
  (`sources.py`), so the coordinator and setup forms don't name any driver.

### Changes
- A poll in which no access point at all can be read no longer makes every entity
  unavailable at once: within the grace period after the last successful poll it is
  ridden out on the previous sightings, so a controller restart or integration reload is
  covered the same way as a single failing AP. Past the grace period the trackers become
  unavailable (not away), and the first refresh still fails, so setup is retried.
- The access point CPU and memory sensors are disabled by default: they change on every
  poll, which is a recorder row per access point per minute.
- The tracked devices' Area sensors only react to area changes of the access points'
  devices, not to every device registry update in the house.
- The sightings file is written at most every 10 minutes instead of every poll (it is
  still written on unload).
- An access point whose settings the driver rejects is skipped with an error in the log
  instead of failing the whole integration, and the form reports it on the field.
- Driver hooks for other kinds of access point: `UNIQUE_FIELD` (the setting that
  identifies an AP, "host" by default, used to refuse adding one twice), `POLL_TIMEOUT`
  (per driver, 45 s by default) and `OWN_DEVICE` / `device_mac()` for drivers whose AP
  is already a device of another integration: that device's area is used and no device
  or sensors are created.

## 0.4.2 — deprecation fix

### Changes
- The area lookup uses Home Assistant's per-config-entry device lookup instead of the
  deprecated `async_get_device` (it would stop working in Home Assistant 2027.8).

## 0.4.1 — one time at a time

### Changes
- Only the time that matches the state is set: while home `arrived_at` holds the arrival
  and `departed_at` is `null`; while away `arrived_at` is `null` and `departed_at` holds
  the time the device left. Automations can trigger on an attribute going from `null` to
  a time. (In 0.4.0 both were always set, `departed_at` holding the previous departure
  while home.)

## 0.4.0 — arrival and departure times

### Features
- Trackers have `arrived_at` and `departed_at` attributes. While home: when the visit
  started and when the previous one ended; while away: when the last visit started and
  when the device left (the time it was last seen). They change only on arrival or
  departure and are kept across restarts.
- `scripts/collect.py` collects data for new access points over SNMP too (`--snmp`): it
  finds the vendor's private MIB from `sysObjectID` and lists likely client tables. There
  is also a UniFi profile (`--profile unifi`). Reports are redacted (MAC, IP and email
  addresses, key-like strings, typed credentials; SNMP settings text is shown as its
  length only) and start with a warning to review and redact by hand before sharing.
- README roadmap: UniFi (per AP over SSH), SNMP drivers, OpenWrt.

### Changes
- The tracker attribute `last_seen` is replaced by `departed_at`.
- A grace period below 30 s acts as 30 s. With 0, a device read in the latest poll was
  already reported away.

## 0.3.0 — first public release

Presence detection for Home Assistant from Wi-Fi access point association tables.
Supports D-Link DAP access points over SSH (tested: DAP-2610, DAP-3662).

### Features
- One presence tracker per device across all access points, with an adjustable grace
  period for roaming and brief gaps.
- **Area** sensor per tracked device: the Home Assistant area of the access point it is
  connected to, for room-level automations.
- **Access point** and **Signal** sensors per tracked device.
- Access point devices with firmware, hardware, client counts per band, CPU, memory,
  last boot and location; unnamed access points use the name they report (e.g. *Studio*).
- Set up entirely in the UI, with access points and tracked devices as entries of one hub.
- The tracked-device list offers every device seen in the last 7 days (kept across
  restarts), most recent first, so sleeping devices like cars can be picked.
- Entity icons and a brand icon.
- `scripts/collect.py` to collect redacted data for supporting new access points.

### Changes since the 0.2 test builds
- Tracker attributes no longer include the signal (see the *Signal* sensor, whose
  attributes hold the raw `signal` and `signal_unit`), and `last_seen` is shown only while
  the device is away. Attributes now change only when the situation does, so the recorder
  no longer writes a row per device on every poll.
- The *Signal* sensor is a 0-100 % quality that is comparable across drivers; roaming
  attribution never prefers an unknown signal over a known one.
- Whether an access point was named by the user is stored explicitly (existing entries
  keep working).
- Spaces around host, username and other settings are stripped during setup.
- D-Link: an error reply such as "Invalid parameter: 9" is no longer taken as a value.
- Presence rules are tested on every push (pytest, ruff, hassfest, HACS validation).
