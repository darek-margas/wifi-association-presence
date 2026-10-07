# Changelog

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
