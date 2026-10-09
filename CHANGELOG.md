# Changelog

## 0.7.0b2 — MikroTik: only authorized clients count (beta)

- MikroTik access points: a client still authenticating doesn't count as connected yet.
  The client-table columns were checked against MikroTik's WiFi manual; the driver stays
  experimental until confirmed on a real MikroTik access point.
- Uses wifi-ap-associations 0.5.1.

## 0.7.0b1 — MikroTik RouterOS access points, experimental (beta)

- **Add access point → "MikroTik RouterOS (SSH)"**, shown as experimental: RouterOS
  access points, routers with Wi-Fi and CAPsMAN controllers. Clients with SSID, band,
  signal and connected time; the access point's name, model, firmware, uptime, CPU and
  memory.
- **Experimental** because only the system details are confirmed so far (RouterOS 7.20
  on a switch). The client table follows MikroTik's documentation. If you have a MikroTik
  access point: add it, and if clients don't show up as expected, send a **Collect access
  point report** with the *MikroTik RouterOS* command list.
- Uses wifi-ap-associations 0.5.0.

## 0.6.1 — better access point reports: MikroTik, timing, CPU and memory over SNMP

Everything since 0.6.0 (the betas below have the details). Nothing changes for your
access points, trackers or settings.

- **Collect access point report → SSH → Command list "MikroTik RouterOS"**: system
  details, interfaces and the client table of each RouterOS Wi-Fi package, read command
  by command (no terminal), in well under a second. Tested on RouterOS 7.20.
- **The result says how long the collection took** ("Report collected in 0.3 s"), and the
  report and diagnostics record it.
- **SNMP reports** also read the standard CPU and memory tables (HOST-RESOURCES).
- **Extra commands** accept the usual client-list commands of Linux, Broadcom, Atheros
  and MikroTik based access points (since library 0.4.2).
- CI copes with PyPI's delay right after a library release.
- Uses wifi-ap-associations 0.4.4.

## 0.6.1b2 — report shows how long it took; MikroTik and SNMP reports read more (beta)

- **"Report collected in 0.8 s"**: the result says how long the collection took, and the
  diagnostics record it (`duration_seconds`). A MikroTik report over SSH takes under a
  second, which looked as if nothing ran. The report text itself ends with
  `# finished: 11 commands in 0.8 s`.
- **MikroTik command list** also reads the Wi-Fi interfaces with their SSID and band.
- **SNMP reports** also read the standard CPU and memory tables (HOST-RESOURCES), which a
  future SNMP driver needs for the access point's health sensors.
- CI retries installing a freshly released library while PyPI's servers catch up.
- Uses wifi-ap-associations 0.4.4.

## 0.6.1b1 — MikroTik command list for access point reports (beta)

- **Collect access point report → SSH → Command list: "MikroTik RouterOS"**: identity,
  resources, routerboard, packages, interfaces and the client table of each RouterOS
  Wi-Fi package (`wireless`, `wifi`, `wifiwave2`, CAPsMAN). The commands run one by one
  instead of in RouterOS's interactive terminal, so the report is readable (no logo,
  escape codes, echoed commands or lines wrapped at 80 columns). A first step towards a
  MikroTik driver.
- Uses wifi-ap-associations 0.4.3.

## 0.6.0 — help add your access point from Home Assistant; drivers as a library

Everything since 0.5.2 (the betas below have the details). Update from HACS and restart;
Home Assistant needs internet access at that restart to install the driver library.
Nothing changes for your access points, trackers or settings.

### New
- **Collect access point report** (a button on the integration page): for an access point
  that isn't supported yet, Home Assistant reads it once, over **SSH** or **SNMP v2c / v3**,
  and keeps a redacted report. Download it with **Download diagnostics** and attach it to
  a *New access point model* issue. Only display commands run (extra commands are
  checked; shell tricks are refused), and passwords, communities and keys are used for
  that collection only, never stored. Closing the window doesn't stop it, and a failed
  attempt is recorded in the diagnostics too.
- **Download diagnostics** for the integration: library version, options, access point
  types, number of tracked devices, and the last report. No addresses or credentials of
  your access points.

### Changes
- **The access point drivers are now a separate library,
  [wifi-ap-associations](https://pypi.org/project/wifi-ap-associations/)** (0.4.2),
  installed by Home Assistant. Other tools can use it too, and it brings the command-line
  tools `wifi-ap-collect` and `wifi-ap-probe`.
- **OpenWrt:** a failed or garbled read of an access point's client table now counts as
  a failed read (devices keep their last state through the grace period) instead of
  "no clients".
- README: the first screen says what it does, how to set it up, and what to do if your
  access points aren't supported yet.

## 0.6.0b6 (beta)

- Use wifi-ap-associations 0.4.1: OpenWrt read failures no longer become empty client lists; custom SSH collector commands are validated; SNMP engines are closed on all exit paths.
- Allow only one AP report collection at a time, including after its dialog closes, so concurrent collections cannot overwrite each other's report.
- Keep only non-secret form suggestions and discard collection credentials on completion, failure or cancellation.

## 0.6.0b5 — access point reports over SNMP, collection survives a closed window (beta)

### New
- **Collect access point report over SNMP**, v2c (community) or v3 (user, keys and
  algorithms), next to SSH: the button first asks how to read the access point. It runs
  the same walk and redaction as `wifi-ap-collect --snmp`: the vendor's own tables and the
  standard wireless tables, the likely client tables listed first, other values shown only
  as their length. Up to 5000 values per table and 5 minutes. Communities and keys are
  used for this collection only and not stored.
- **Closing the window doesn't stop a collection.** The progress window says so, and
  where the result appears: Download diagnostics.
- **A failed collection is recorded in the diagnostics** (`last_attempt`, e.g.
  `failed: invalid_auth`), so you can see what happened after closing the window. An
  earlier successful report is kept.

### Changes
- The diagnostics record how the report was collected: method, port, and for SSH the
  command list, extra commands and legacy SSH; for SNMP v3 the algorithms. Usernames,
  passwords, communities and keys are never recorded.
- Releases now wait for all tests, including the ones that run the integration in a test
  Home Assistant (0.6.0b4 was released while those were failing).
- The "Report collected" message starts with a warning to read the whole report before
  submitting it, links to the integration page, and says where Download diagnostics is
  (now and later) and what else the file contains.
- Uses wifi-ap-associations 0.4.0, which includes pysnmp. Home Assistant imports pysnmp
  only when you collect over SNMP, so startup doesn't change.

## 0.6.0b4 — "Collect access point report" button (beta)

### Changes
- **The report collector has its own button** on the integration page, **Collect access
  point report**, next to *Add access point* and *Add tracked device*. In 0.6.0b3 it was
  hidden behind the hub's gear (Configure). It works the same: the same form, progress
  and Download diagnostics, and it adds nothing to your setup.
- **The hub's gear opens the grace period directly again**, without the menu of 0.6.0b3.

## 0.6.0b3 — collect an access point report from Home Assistant (beta)

### New
- **Collect a report from an access point, in Home Assistant.** For an access point that
  isn't supported yet: Configure → *Collect a report from an access point*, enter its
  address and SSH login, pick a command list, and Home Assistant runs the same read-only,
  redacting collector as `wifi-ap-collect`. Then **Download diagnostics** on the
  integration page and attach the file to a *New access point model* issue. No Python
  or computer needed. The password is used for that login only and not stored; the
  report is kept in memory until Home Assistant restarts or the next collection. SSH
  only for now (SNMP still with `wifi-ap-collect --snmp`).
- **Download diagnostics** now exists for the integration: the library version, your
  options, the access point types, the number of tracked devices, and the last collected
  report. It contains no addresses or credentials of your configured access points.

### Changes
- **Configure is now a menu:** *Grace period* (the setting that was there before) or
  *Collect a report from an access point*.
- Uses wifi-ap-associations 0.3.0 (adds the collector as a function; drivers unchanged).
- The *New access point model* issue form accepts the diagnostics file.

## 0.6.0b2 — collector and probe are library commands (beta)

### Changes
- **The collector and the probe moved to the library** (wifi-ap-associations 0.2.0) as
  the commands `wifi-ap-collect` and `wifi-ap-probe`. To send a report for a new access
  point: `pip install wifi-ap-associations`, then `wifi-ap-collect --host <AP IP>
  --username <user>` (SNMP also needs `pip install pysnmp`). They work as before; the
  library adds tests for the collector's redaction and read-only rules.
- `scripts/collect.py` and `scripts/probe.py` stay as small forwarders to the new
  commands, so older instructions keep working once the library is installed.
- README, the *New access point model* issue form and the OpenWrt VM guide use the new
  commands.
- Nothing changes in Home Assistant: the drivers are the same as in 0.6.0b1.

## 0.6.0b1 — access point drivers moved to a library (beta)

### Changes
- **The access point drivers are now the library
  [wifi-ap-associations](https://pypi.org/project/wifi-ap-associations/)** (0.1.0), which
  Home Assistant installs with the integration. The code is the same as in 0.5.2, so
  nothing changes for your access points, devices or settings. It is released and tested
  on its own, and other tools can use it.
- The driver tests and their fixtures moved with the code; `scripts/probe.py` now needs
  `pip install wifi-ap-associations`.

## 0.5.2 — no joined visits after a crash

### Fixes
- **A crash or power cut no longer joins visits across the outage.** After a restart, a
  device that was home when the sightings were last written continues its visit, so
  Home Assistant being down doesn't fake a departure and an arrival. That is right after
  a normal stop, when the file is written at the moment Home Assistant stops. After a
  crash or power cut the last write can be up to 10 minutes old, and a device that left
  after it and came back during the outage kept its old `arrived_at`, hiding the
  departure. The file now records whether it was written at stop (or unload); only then
  is the downtime bridged. After an unclean shutdown a device seen again starts a new
  visit. Upgrading keeps visits as before: files from 0.5.0 / 0.5.1 count as written at
  stop.
- No periodic write can replace the one made at stop.

### Changes
- Tests pin down the restart rules: a long normal shutdown keeps only the visits of
  devices that were home at the stop, and with a short grace period a restart decides
  exactly as uninterrupted polling would. (Thanks to a review pointing at this code.)
- README: how restarts and crashes are handled, and why a failing AP lets its devices go
  `not_home` while all APs failing makes the trackers unavailable.
- README brought up to date with three AP types instead of reading as D-Link only: a
  table of what each type reports (name, model, firmware, location, CPU, memory, signal
  unit, login), security notes for all SSH types (no host-key check for OpenWrt either),
  D-Link specifics moved to its setup notes, and the plan to move the AP drivers into a
  standalone library.

## 0.5.1 — UniFi confirmed

### Changes
- **UniFi is no longer experimental.** David Coulson confirmed the UniFi access point
  type on his UniFi Network controller, so it is listed without "(experimental)" in the
  type list. No other change: existing UniFi access points keep working as they are.

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

