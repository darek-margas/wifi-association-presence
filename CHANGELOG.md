# Changelog

## 0.10.0b3 — instant arrivals: only arrivals and moves (beta)

- MikroTik instant arrivals now poll only when a client **joins** (once it is
  authorized) or **moves** to another CAP or radio. A device that keeps
  re-authenticating no longer makes the integration poll the router every few seconds,
  and an arrival is no longer polled for too early, before the client counts as
  connected. Built from a tester's real capture.
- Uses wifi-ap-associations 0.10.2.

## 0.10.0b2 — review fixes (beta)

- Instant arrivals: a burst of changes from a busy controller now makes one refresh
  request instead of one per change; a client joining during a refresh is still picked
  up.
- Cisco over SNMP: signal and connected time each keep their last value on their own
  when a read misses one of them, and a failing signal column no longer makes the
  access point unavailable.
- Uses wifi-ap-associations 0.10.1.

## 0.10.0b1 — instant arrivals for MikroTik, experimental (beta)

- **Instant arrivals.** MikroTik access points and CAPsMAN controllers now tell the
  integration when a client joins: it keeps
  `registration-table print follow-only` running over the SSH login it already has, and
  every change makes it poll at once. An arrival shows in a few seconds instead of up to
  a minute, for your arrival automations.
- The poll still decides who is there; departures still wait for the grace period. A
  burst of changes is one poll (at most one every 5 seconds), and the minute's regular
  poll goes on regardless.
- If the connection drops or the router ends it, the integration reconnects after a
  growing wait (5 s up to 5 minutes) and logs once when it pauses and once when it works
  again. Nothing else changes while it's paused.
- **On by default**, with an **Instant arrivals** switch in **Configure**. It keeps one
  SSH connection open per MikroTik access point or CAPsMAN controller.
- Experimental: the exact output of `follow-only` hasn't been seen from a device yet.
- Uses wifi-ap-associations 0.10.0.

## 0.9.0 — Cisco Catalyst 9800, MikroTik CAPsMAN, and a wiki

Everything since 0.7.1 (the 0.8.0 and 0.9.0 betas below have the details), for everyone.
Update from HACS and restart; nothing to change in your setup.

- **Cisco Catalyst 9800 access points**, through their controller: add one access point
  per Cisco AP, with the controller's address and login and the AP's name.
  - **Over SSH:** clients with SSID, band, signal and connected time, and the AP's name,
    model and location. Confirmed on a C9800-CL. A restricted parser view is enough;
    the [wiki](https://github.com/darek-margas/wifi-association-presence/wiki/Cisco-Catalyst-9800)
    has the controller setup.
  - **Over SNMP** (v2c or v3), for controllers without SSH: the same, with signal for
    every client on every poll. Still **experimental**.
- **MikroTik, no longer experimental.**
  - **MikroTik CAP via CAPsMAN:** add one access point per CAP, with the CAPsMAN
    router's login and the CAP's name; each client lands on the CAP it is connected to,
    so each CAP can have its own area.
  - **Single MikroTik access points:** clients are found again on recent RouterOS where
    the built-in `wifi` package is unused; signal and connected time now show for the
    `wireless` package; clients on an extra SSID get their band.
- **Coverage counts survive a restart:** Roams, Short drops and Late roams today are kept
  across a restart during the day.
- **Access point settings:** two controllers may each have an AP of the same name; the
  same AP added twice (e.g. over SSH and SNMP) is refused; SNMP has a version setting,
  so switching between v2c and v3 needs no clearing of fields.
- **Access point reports:** SNMP reports have a *Host type* (Cisco Catalyst 9800, Cisco
  wireless controller) that reads just the controller's client, signal and AP tables;
  MikroTik reports include CAPsMAN radios and CAPs.
- **A [wiki](https://github.com/darek-margas/wifi-association-presence/wiki)** with a
  setup guide for every type of access point and a troubleshooting page.
- Uses wifi-ap-associations 0.9.3.

## 0.9.0b3 — MikroTik wireless signal fixed (beta)

- **Fix: MikroTik access points on the `wireless` package still showed no signal in
  0.9.0b2.** RouterOS spreads each client's statistics over several lines, and the
  signal is on the second one; the integration now reads them as one. Signal and
  connected time show for these clients now.
- Uses wifi-ap-associations 0.9.2.

## 0.9.0b2 — coverage counts kept across a restart; MikroTik follow-ups (beta)

- **"Roams today", "Short drops today" and "Late roams today" survive a restart** on
  the same day. They are saved with the tracked devices (when Home Assistant stops and
  every 10 minutes) and restored at start if it's still the same day; at midnight they
  start again as before. A roam or drop across the downtime itself isn't counted:
  nobody saw where the device was meanwhile.
- **The CAPsMAN type is now "MikroTik CAP via CAPsMAN (SSH)"**, since you add it once
  per CAP. Access points already added keep working; only the name in the list changes.
- **MikroTik with the `wireless` package now shows signal and connected time.** RouterOS
  leaves them out of the client list the integration read; they now come from the same
  list with statistics (`print stats terse`). Presence doesn't depend on it.
- Uses wifi-ap-associations 0.9.1.

## 0.9.0b1 — MikroTik CAPsMAN: one access point per CAP, experimental (beta)

- **Add access point → "MikroTik CAPsMAN controller (SSH)"**, shown as experimental
  ([#11](https://github.com/darek-margas/wifi-association-presence/issues/11)). With
  CAPsMAN the controller lists the clients of all CAPs, so until now the router was one
  access point and everyone ended up in its area. Now you add **one access point per
  CAP**: the controller's address and login, and the CAP's identity as the controller
  lists it (e.g. `ap_upstairs`). Each client is put on the CAP it is connected to, with
  SSID, band, signal and connected time, so each CAP can have its own area. All CAPs
  share one login per poll.
- Each CAP shows its model, RouterOS version and uptime. A CAP the controller doesn't
  list or that is disconnected shows as unavailable; nobody is sent away because of it.
- For RouterOS 7 with the `wifi` package (CAPsMAN v2). The older `/caps-man` isn't
  covered yet.
- **If you had the CAPsMAN router added as a plain MikroTik access point**, remove it
  once its CAPs are added: it lists the same clients and would count them twice.
- Uses wifi-ap-associations 0.9.0.

## 0.8.0b11 — MikroTik: clients found again on recent RouterOS (beta)

- **Fix: a MikroTik access point on recent RouterOS reported no clients**
  ([#12](https://github.com/darek-margas/wifi-association-presence/issues/12)), so
  everyone on it looked away. RouterOS 7 now has the new `wifi` package built in even
  where every radio uses the older `wireless` package (e.g. a cAP lite on 7.24.4), and
  the integration read the new package's empty client list instead of the old one. It
  now reads both and combines them.
- MikroTik: clients on an extra SSID (a virtual interface) now show their band.
- Uses wifi-ap-associations 0.8.5.

## 0.8.0b10 — MikroTik CAPsMAN: reports with what's needed for per-CAP areas (beta)

- **The groundwork for MikroTik CAPsMAN with one access point per CAP**
  ([#11](https://github.com/darek-margas/wifi-association-presence/issues/11)). Today a
  CAPsMAN router is one access point: every client of every CAP counts as connected to
  the router, so presence works but everyone ends up in the router's area. To put each
  client on its CAP, the integration needs to know which CAP each Wi-Fi interface
  belongs to.
- **Collect access point report → SSH → MikroTik RouterOS** now also reads the CAPsMAN
  radios, CAP interfaces and remote CAPs (`/interface wifi radio`,
  `/interface wifi capsman remote-cap`, `/caps-man interface`, `/caps-man remote-cap`,
  each `print terse`). If you use CAPsMAN, a report from the controller is what's needed
  to add per-CAP support; attach it to #11.
- Nothing changes for MikroTik access points in use: presence, SSID, band and signal are
  read as before.
- Uses wifi-ap-associations 0.8.4.

## 0.8.0b9 — Cisco SNMP: no stale signal (beta)

- Cisco over SNMP: when the controller stops reporting a client's signal or connected
  time, the last measurement is shown for at most about two polls and then the value is
  unknown, instead of the old reading staying on for good.
- Uses wifi-ap-associations 0.8.3.

## 0.8.0b8 — Cisco: signal over SNMP; SSH no longer experimental (beta)

- **Cisco Catalyst 9800 over SNMP now has signal and connected time**, for every client
  on every poll (no turns as over SSH), from the controller's client statistics and
  Cisco's client table. The tester's two SNMP reports showed where they are.
- **Cisco over SSH is no longer experimental:** confirmed on a C9800-CL in Home
  Assistant, with every client, AP and band matching the controller and signal working.
  A privilege 1 user gives presence only; signal needs the client detail command, which
  a restricted parser view can allow without privilege 15. The tester's working setup
  is in the [wiki](https://github.com/darek-margas/wifi-association-presence/wiki/Cisco-Catalyst-9800).
- Over SNMP it stays experimental until it has run with signal in Home Assistant.
- Uses wifi-ap-associations 0.8.2.

## 0.8.0b7 — steadier Cisco signal readings (beta)

- Cisco over SSH: signal readings of devices that stay put keep being refreshed even
  while many new devices connect, so *Average client signal* and *Weak clients* don't
  dip just because readings expired.
- The time spent on reading client details each poll stays within its 10 s budget.
- Uses wifi-ap-associations 0.8.1.

## 0.8.0b6 — Cisco over SSH: signal and connected time (beta)

- **Cisco Catalyst 9800 over SSH now shows each client's signal (dBm) and connected
  time**, so *Average client signal* and *Weak clients* work for these APs. They come
  from each client's detail on the controller, read for up to 15 clients per poll, in
  turns: new and roamed clients first, then the oldest readings. With a couple of dozen
  clients, everyone has a signal after the second poll and is refreshed about every 2
  minutes.
- A signal is shown only while the client stays on the access point it was read on, so
  after a roam it is empty until the next reading (usually the next poll).
- If the controller doesn't allow the detail command for your user, signal stays empty
  and presence works as before.
- Over SNMP: no signal yet.
- Uses wifi-ap-associations 0.8.0.

## 0.8.0b5 — Cisco controller reads can't get stuck (beta)

- A controller read that hangs is stopped after 45 s, so the next poll tries again
  instead of waiting on it; the APs of that controller recover on their own.
- Less memory kept between Cisco SNMP reads.
- Uses wifi-ap-associations 0.7.2.

## 0.8.0b4 — Cisco fixes from a review (beta)

- **Two Cisco controllers may each have an AP01.** An access point is now identified by
  what it points at: a host, a UniFi AP, or a controller's host plus the AP's name. The
  same Cisco AP added both over SSH and over SNMP is refused as already configured: it
  would count its clients twice.
- **Cisco over SNMP has an SNMP version setting** (v2c or v3). It decides which
  credentials are used, so switching from v3 to v2c (or back) in the access point's
  settings just works; the other version's fields may stay filled in.
- **Cisco over SNMP reads faster and steadier:** all columns at once, and a client caught
  mid-roam keeps its access point for one more read instead of looking gone.
- After a failed read, the next poll tries the controller again at once.
- Uses wifi-ap-associations 0.7.1.

## 0.8.0b3 — Cisco Catalyst 9800 over SNMP, experimental (beta)

- **Add access point → "Cisco Catalyst 9800 controller (SNMP)"**, shown as
  experimental: the same Cisco APs, for controllers you can read over SNMP but not SSH.
  One access point per Cisco AP: the controller's address, the AP's name, and either the
  read-only community (SNMP v2c) or a user with keys and algorithms (SNMP v3; leave the
  community empty).
- Clients with SSID and band, the AP's name, model and location, as over SSH. Only
  clients in the Run state count. No signal yet. An AP that isn't joined, wrong keys or
  no answer make the AP unavailable instead of sending everyone away.
- Experimental because it's built from one controller's SNMP report (#10). If you have a
  9800 with SNMP: add an AP and compare with `show wireless client summary`.
- The access point form can now leave settings empty and offer lists (the SNMP v3
  algorithms).
- Uses wifi-ap-associations 0.7.0.

## 0.8.0b2 — SNMP reports from Cisco controllers read the right tables (beta)

- **Collect access point report → SNMP** has a **Host type**: *Generic* (as before),
  *Cisco Catalyst 9800* or *Cisco wireless controller*. With a Cisco host type only the
  controller's client, client signal and AP tables are read, complete, instead of Cisco's
  whole MIB, which ran out of its 5000-value limit before reaching them.
- Cisco SNMP reports now include the client statistics table, where each client's signal
  is, and Cisco's whole client MIB. A C9800 report had no signal because neither was read.
- Uses wifi-ap-associations 0.6.1.

## 0.8.0b1 — Cisco Catalyst 9800 access points, experimental (beta)

- **Add access point → "Cisco Catalyst 9800 controller (SSH)"**, shown as experimental:
  lightweight Cisco APs (9120, 9130, ...) joined to a Catalyst 9800 controller (IOS-XE).
  Add one access point per Cisco AP: the controller's host, username and password, and
  the AP's name as the controller lists it (`show ap summary`, e.g. AP01). APs of the
  same controller share one login per poll.
- Clients with SSID and band; the AP's name, model and location (if set on the
  controller). Only clients in the Run state count as connected. **No signal yet**, so
  Average client signal and Weak clients stay empty for these APs.
- An AP that isn't joined or isn't Registered, a command the controller refuses (e.g. a
  user with too little privilege) and an incomplete client table make the AP unavailable
  instead of sending everyone away.
- **Experimental** because it's built from one controller's report
  ([#10](https://github.com/darek-margas/wifi-association-presence/issues/10), a
  C9800-CL on IOS-XE 17.15.5), not yet run against a controller. If you have one: add an
  AP, and if clients don't show up as expected, send a **Collect access point report**
  with the *Cisco WLC* command list.
- Uses wifi-ap-associations 0.6.0.

## 0.7.1 — coverage counter fixes

Coverage statistics only; home / away is unchanged.

### Fixes
- **Roams were missed with a short grace period.** With the grace period at 60 s or less
  (polls are 60 s apart), a device that moved to another access point between two polls
  wasn't counted as a roam. Consecutive polls are now compared whatever the grace period.
- **A total outage could count false roams.** When no access point could be read, the
  coverage counters kept their last observations, so a device that came back on another
  access point counted as a roam (and maybe a late roam) although nobody saw it move.
  Observation now stops during such an outage.
- Tests at the real 60 s poll interval, and an outage-and-recovery test in Home Assistant.
  (Thanks to a review that reproduced both.)

## 0.7.0 — Wi-Fi coverage dashboard; MikroTik RouterOS (experimental)

Everything from the 0.7.0 betas, for everyone:

- **How good your Wi-Fi is.** These are new sensors, counted from the polls the
  integration already makes:
  - **For each access point:** **Average client signal** and **Weak clients** (below
    50 %, about -75 dBm).
  - **For each tracked device:** **Roams today**, **Short drops today** (gaps that
    presence hides) and **Late roams today** (stayed too long on a far access point).
  - The counts are kept in memory and start again at midnight and when Home Assistant
    restarts.
- **[Coverage dashboard](docs/coverage-dashboard.md):** paste one template into
  *Developer tools → Template*, and it writes the whole dashboard with your own access
  points and devices. There are no entity ids to look up. The guide explains each sensor
  and how to read it.
- **MikroTik RouterOS access points (experimental),** over SSH:
  - clients with SSID, band, signal and connected time;
  - the access point's model, firmware, uptime, CPU and memory;
  - only authorized clients count.
  - It stays experimental until confirmed on a MikroTik access point that has clients.
- Uses wifi-ap-associations 0.5.1.

## 0.7.0b4 — coverage counter fixes (beta)

- A device coming back after more than the grace period isn't counted as a roam, even
  when it returns on the first poll after the grace period ends.
- A gap is measured from when the device was last seen, as presence does. So a short drop
  is exactly a gap during which the tracker stayed home.
- When a device's access point can't be read, that gap or switch isn't counted. Nobody can
  tell what happened during it.
- The daily counts start again at midnight even while no access point can be read.
- Dashboard guide: the short-drops sensor is `sensor.<device>_short_drops_today`.

## 0.7.0b3 — Wi-Fi coverage sensors and dashboard (beta)

- **New per access point** (D-Link, OpenWrt, MikroTik):
  - **Average client signal** (0-100 %).
  - **Weak clients** (below 50 %, about -75 dBm).
- **New per tracked device:**
  - **Roams today**: moved to another access point.
  - **Short drops today**: vanished and came back within the grace period, which
    presence hides.
  - **Late roams today**: switched access point and gained 15 dB or more.
- The integration counts these from the polls it already makes. The counts are kept in
  memory and start again at midnight and when Home Assistant restarts. Clients of an
  access point that couldn't be read don't count as dropped. A device away longer than
  the grace period has left, so that isn't a drop.
- New [coverage dashboard](docs/coverage-dashboard.md) guide. It explains each sensor and
  has a ready-made dashboard: plain cards, or auto-entities for all devices at once.
- Uses wifi-ap-associations 0.5.1, unchanged.

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

