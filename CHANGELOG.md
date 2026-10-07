# Changelog

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
