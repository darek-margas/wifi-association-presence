<img src="docs/images/icon.svg" alt="" width="96" align="right">

# Wi-Fi Association Presence

**Home Assistant presence from your Wi-Fi access points: who is home, and in which room.**

- **Home / away** for each phone, tablet or laptop, steady while the phone sleeps.
- **Which room**: the area of the access point it is connected to, and its signal.
- **When it arrived or left**, to the minute, for automations.
- **Access points as devices**: connected clients, firmware, CPU, memory, uptime.
- **Nothing to install on the phones.** It reads the list of devices joined to each
  access point, from the access point itself or from its controller.

[Supported access points](#supported-access-points) ·
[Install](#installation) ·
[Help add yours](#help-add-your-access-point) ·
Early development, Home Assistant 2026.9+

## Why association, not ARP or MAC tables

Most network-based presence trackers look at a router's **ARP table**, a switch's
**MAC address table**, or ping the device. All of these only notice a phone while it is
*sending traffic*, and phones go quiet for minutes at a time to save battery. The result
is the familiar flapping: home, away, home, while the phone sat on the table.

An access point knows something more fundamental: whether the device is **associated**,
i.e. still joined to the Wi-Fi network. A sleeping phone stays associated. It only
disassociates when it really leaves (or Wi-Fi is turned off).

| | ARP table | Switch MAC table | Ping | **AP association** |
|---|---|---|---|---|
| Sees an idle, sleeping phone | ✗ entries age out | ✗ entries age out | ✗ often no reply | **✓ stays associated** |
| Knows *where* in the house | ✗ | ✗ | ✗ | **✓ which AP, so which area** |
| Signal strength | ✗ | ✗ | ✗ | **✓** |
| Wired devices | ✓ | ✓ | ✓ | ✗ Wi-Fi only |

On top of that, a short **grace period** (default 3 minutes) bridges roaming between APs
and brief gaps, so `not_home` means *gone*, not *quiet*.

## Features

### Presence that doesn't flap
- One tracker per device, **across all your access points**: `home` while associated to
  any of them, `not_home` once missing for longer than the grace period.
- **Roaming aware**: a device seen on two APs in the same poll is placed on the one with
  the stronger signal.
- **Adjustable grace period** (0–3600 s; below 30 s it acts as 30 s, i.e. away after one
  missed poll) for phones that drop Wi-Fi in deep sleep.

### Room-level location with areas
- Assign each access point device to a Home Assistant **area** (Living room, Studio,
  Garden...). Every tracked device then gets an **Area** sensor showing the area of the
  access point it is connected to, with a stable `area_id` attribute.
- Use it in automations: *lights on in the studio when my phone joins the studio AP*,
  *notify when the kids' tablets are in the garden*, *which floor is everyone on*.
- It reacts immediately when you move an AP to another area or rename an area.

### Arrival and departure times
Every tracker tells you **when** the device arrived or left, not just whether it is home:

| Tracker state | `arrived_at` | `departed_at` |
|---|---|---|
| `home` | when it arrived | `null` |
| `not_home` | `null` | when it left |

- **Accurate times.** The departure is the moment the device was last seen associated,
  not the moment the grace period ran out, so "left at 08:12" means 08:12 (to within one
  poll, a minute), even with a 3-minute grace period. Coming back within the grace period continues the same
  visit, so roaming and short gaps don't produce a fake departure and arrival.
- **Made for automations.** Exactly one of the two holds a time, matching the state.
  Trigger on an attribute going from `null` to a time and you get one clean event per
  arrival or departure, with the exact time in it.
- **Survives restarts.** Arrival and departure times are stored, so a restart doesn't
  turn "home since 07:40" into "home since the restart".
- **Light on the database.** The attributes change only on arrival or departure, so the
  recorder writes a row per event, not per poll. The tracker's History and Logbook then
  list every arrival and departure.

Examples (replace `device_tracker.darek_phone` with your tracker):

```yaml
# Tell me when someone leaves, with the time they actually left.
triggers:
  - trigger: state
    entity_id: device_tracker.darek_phone
    attribute: departed_at
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.departed_at is not none }}"
actions:
  - action: notify.notify
    data:
      message: >-
        Darek left at
        {{ as_timestamp(trigger.to_state.attributes.departed_at) | timestamp_custom('%H:%M') }}
```

```yaml
# Welcome home: lights on when an arrival is recorded after dark.
triggers:
  - trigger: state
    entity_id: device_tracker.darek_phone
    attribute: arrived_at
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.arrived_at is not none }}"
  - condition: sun
    after: sunset
actions:
  - action: light.turn_on
    target:
      area_id: hallway
```

```jinja
{# Dashboard text: "home for 2 hours" / "away for 3 days" #}
{% set t = 'device_tracker.darek_phone' %}
{% if is_state(t, 'home') %}home for {{ state_attr(t, 'arrived_at') | as_datetime(default=none) | relative_time }}
{% else %}away for {{ state_attr(t, 'departed_at') | as_datetime(default=none) | relative_time }}{% endif %}
```

A device that was already home when you installed or upgraded to 0.4 shows that moment
as its arrival until it next leaves and comes back.

### Per device insight
Each tracked device is a Home Assistant device with:
- **Tracker** with attributes `access_point`, `area`, `ssid` and `band` (kept after the
  device leaves, as "last seen at"), plus `arrived_at` / `departed_at`
  ([see above](#arrival-and-departure-times)).
- **Access point** sensor: the AP's name (yours, or the name the AP reports, e.g. *Studio*).
- **Signal** sensor: signal quality 0-100 %, comparable across access points and vendors
  (percent as reported by D-Link; dBm from other drivers is converted), with a matching
  Wi-Fi strength icon. Its attributes hold the raw value and unit (`signal`, `signal_unit`).
- **Area** sensor, as above.

### Access point monitoring
Each access point is a Home Assistant device with firmware, hardware revision, model, a
link to its web UI, and sensors:
- **Clients 2.4 GHz**, **Clients 5 GHz** and **Clients** (total).
- Diagnostics: **Last boot**, **Location**, and **CPU** and **Memory** (disabled by
  default: they change every poll, so each would be a recorder row per minute).
- SSID names resolved from the AP (not just "SSID index 3").

### Easy and robust
- **Set up entirely in the UI.** Access points and tracked devices are entries of the
  integration: add, edit and remove them from its page. Pick devices to track from a list
  of everything seen in the last 7 days, so a sleeping car or tablet can be picked too.
- **Fault tolerant.** An unreachable, slow or misbehaving access point only affects itself:
  its sensors go unavailable and its clients age out after the grace period, while the
  other APs keep updating. Unexpected replies are discarded, not shown as values.
- **Independent trackers.** Trackers are keyed by MAC address only, so you can remove,
  replace or change the type of an access point without losing them.
- **Pluggable drivers.** Each AP model is a small driver with its own connection policy;
  new vendors can be added without touching the rest.

## Screenshots

The integration page: one hub, with access points and tracked devices as entries. The
access point shows as *Studio*, the name it reports about itself.

![Integration page](docs/images/integration-page.png)

An access point device: firmware, hardware, client counts per band, CPU, memory, last boot.

![Access point device](docs/images/access-point-device.png)

A tracked device: its tracker, the access point it is connected to, the area of that
access point, and the signal.

![Tracked device](docs/images/tracked-device.png)

## How presence is decided

Every 60 seconds all access points are read in parallel (one SSH login per AP per poll).
A device seen on two APs in the same poll (while roaming) is attributed to the one with
the stronger signal.

| Situation | Tracker state |
|---|---|
| Associated to any AP now | `home` |
| Not seen for less than the grace period | still `home` |
| Not seen for longer than the grace period | `not_home` |
| No access point configured | `unknown` |
| No access point could be read at all | unavailable |

Each stay is a **visit**: it starts when the device is first seen (`arrived_at`) and ends
when it was last seen before going away (`departed_at`). Coming back within the grace
period continues the same visit, so roaming and short gaps don't reset "home since".
Sightings and visits are kept for 7 days and survive restarts.

Tracked devices are independent of access points. Removing, replacing or changing the
type of an access point doesn't touch them.

## Installation

Requires Home Assistant **2026.9** or newer.

**HACS:** HACS → ⋮ → **Custom repositories** → add this repository's URL with type
**Integration**, install **Wi-Fi Association Presence**, and restart Home Assistant.

**Manual:** copy `custom_components/wifi_association_presence` to
`/config/custom_components/` and restart Home Assistant.

## Setup

1. **Settings → Devices & services → Add integration → Wi-Fi Association Presence.**
2. On the integration page, **Add access point**: choose the type, then its settings.
   The settings are tested by reading the AP once. Leave the name empty to use the name
   the AP reports about itself (D-Link: its system name, e.g. "Studio").
3. Open each access point's device and set its **Area** (✏️ → Area). This is what the
   trackers' *Area* sensor reports.
4. **Add tracked device**: pick a device from the list or type its MAC address, and give it
   a name. The list holds every device seen in the last 7 days, most recent first, labelled
   with AP, band, SSID and signal; devices not connected right now (a sleeping car or
   tablet) show when they were last seen. This memory survives restarts.
5. Optional: **Configure** on the hub sets the grace period.

Access points and tracked devices can be edited from their **⋮** menu (**Reconfigure**).
Leaving an access point's password empty keeps the current one.

## Supported access points

| Type | Models | Method |
|---|---|---|
| D-Link DAP (SSH console) | **Tested:** DAP-2610 (fw v2.06, [report](docs/ap-reports/dlink-dap-2610-v2.06.txt)), DAP-3662. **Likely:** other DAP models with the same CLI | SSH: `config wlan 0/1` + `get clientinfo` |
| UniFi (via the UniFi Network integration) | Any UniFi AP, or console/gateway with built-in Wi-Fi, managed by a UniFi Network application that Home Assistant's [UniFi Network](https://www.home-assistant.io/integrations/unifi/) integration is connected to | The controller's active client list (`stat/sta`), over the UniFi integration's existing session |

### Two ways to read an access point

Each type of access point is a driver, and there are two kinds:

- **Directly from the AP** (D-Link today; SSH or SNMP to the AP itself). Works without any
  controller, and with only the APs you are allowed to log in to.
- **Through a controller** (UniFi today, via Home Assistant's UniFi Network integration).
  No login per AP and one request for all of them, but you need access to the controller.

Both can exist for the same vendor and can be mixed in one installation, because every
access point is its own entry and trackers don't care which driver saw a device. That
matters outside a simple home network: in an office or a shared building you may be
given access to the APs in your area but not to the controller, or your area may be
covered by APs of two different controllers of which you only need a few. Either way,
each AP keeps its own device and area, so room-level presence works the same.

### UniFi setup notes

- Set up Home Assistant's **UniFi Network** integration first. No credentials are entered
  here: this integration reuses that one's connection to the controller.
- **Add access point → UniFi (via the UniFi Network integration)**, then pick the AP from
  the list (one access point entry per physical AP). APs already added are left out of
  the list.
- No new device or AP sensors are created: the entry attaches to the AP's **existing
  UniFi device**, and the area you set on that device is what the tracked devices' Area
  sensor reports. UniFi already provides the AP's client count, uptime, CPU and memory
  there.
- Each poll requests the controller's active client list once, shared by all its APs; a
  client is associated to an AP while the controller lists it there. Signal is in dBm.
- An AP the controller reports as offline (or not adopted) counts as unreadable, and
  tracked devices on it fall back to the grace period, as with a failing D-Link AP.
- The UniFi integration's own device trackers can stay enabled or be disabled; they are
  independent of this integration's trackers.

### D-Link DAP setup notes

- Enable SSH under **Maintenance → Administration → Console Settings** (protocol SSH).
- The AP only supports password login. Limit management access with
  **Limit Administrator** to your Home Assistant host.
- `get clientinfo` lists the radio selected with `config wlan 0` (2.4 GHz) or
  `config wlan 1` (5 GHz); `set band` does not change it. The `config wlan` command is
  missing from the AP's own `help` output but documented in D-Link's CLI manuals.

## Limitations

**Access point support**
- **D-Link DAP only.** No other vendor or model has a driver yet. Support for more depends
  on owners contributing data (see [Help add your access point](#help-add-your-access-point)
  and the [Roadmap](#roadmap)); the driver interface is designed for it.
- **What the D-Link CLI provides is about all there is.** On the tested firmware it gives
  the client list per radio (MAC, SSID, signal, connected time), SSID names, system
  name, location, firmware, hardware revision, uptime, CPU and memory. It does **not**
  report:
  - the model (enter it in the access point's settings if you want it on the device);
  - the AP's MAC address (`get macaddress` fails on this firmware);
  - clients' IP addresses or host names, so trackers have no `ip`/`host_name`, and Home
    Assistant features that rely on a tracker's IP aren't available.
- Signal quality is an approximation: D-Link reports a percentage, and dBm values are
  mapped linearly (-100 dBm = 0 %, -50 dBm = 100 %).

**Presence**
- **Wi-Fi only.** Wired devices, and devices on networks served by other equipment, are
  not seen.
- **Detection delay:** arriving is detected within one poll (up to 60 s); leaving takes
  the grace period plus up to one poll. The poll interval is fixed by design (Home
  Assistant integrations don't expose polling intervals); the grace period is adjustable.
- **Private / randomized MAC addresses:** track the address the device uses on *your*
  network (phones show it per network). Devices that rotate their address periodically
  (e.g. iOS "Rotating" private address) will stop matching; set them to a fixed private
  address for your network.
- **Devices that drop Wi-Fi to save power** (some Android phones, laptops in sleep)
  disassociate and will show as away unless the grace period covers the gap.

**Security**
- The D-Link units only offer password login and old SSH algorithms
  (`diffie-hellman-group14-sha1`, `diffie-hellman-group1-sha1`, `ssh-rsa`, CBC ciphers).
  The driver enables those for these units only, and does **not verify the host key**
  (it changes on factory reset). Keep management access restricted to the Home Assistant
  host and preferably on a trusted network segment.
- The AP password is stored in Home Assistant's configuration storage, like other
  integrations' credentials.
- Each poll is a console login; the AP may log every login (syslog noise).

**Project state**
- Early development: limited testing (two AP models, one installation), English only, not
  in the default HACS list (add it as a custom repository).

## Help add your access point

You don't need to write code to get an access point supported. Run the collector against
it and attach the report to a
[New access point model](../../issues/new?template=new_access_point.yml) issue.

> **Read the report before you submit it, and redact by hand what is still private.**
> The collector removes MAC, IP and email addresses, key-like strings, the credentials
> you typed and settings with secret-looking names, but no automatic redaction recognises
> everything: a Wi-Fi passphrase, names, locations or serial numbers can look like
> ordinary text. Replace anything you don't want public with `<redacted>`; the structure
> is what matters for adding support. Issues are public.

**SSH console**

```bash
pip install asyncssh
python3 scripts/collect.py --host <AP IP> --username <user>
```

- It runs **read-only commands only** (anything that could change settings is refused)
  and **redacts** MAC addresses (vendor prefix kept), IP and email addresses, key-like
  strings and secret-looking settings. JSON replies are split one key per line so secret
  keys are caught too.
- `--legacy-ssh` allows old SSH algorithms if the connection fails.
- `--command "<cmd>"` (repeatable) adds the command your AP uses to list clients.
- `--profile dlink_dap` uses the D-Link command list, `--profile unifi` the UniFi one
  (`info`, `mca-dump`; log in with the device SSH credentials set in the UniFi
  controller); `--list-profiles` shows all.

**SNMP**

```bash
pip install pysnmp
python3 scripts/collect.py --host <AP IP> --snmp                    # v2c, asks for the community
python3 scripts/collect.py --host <AP IP> --snmp --snmp-user <user> # v3 (SHA / AES-128)
```

There is no standard SNMP table of associated Wi-Fi clients: every vendor keeps it in its
own private MIB, if it offers one at all. The collector finds the vendor's subtree from
the AP's `sysObjectID` (its enterprise number, e.g. 171 for D-Link, 14988 for MikroTik),
walks it together with the standard 802.11 and bridge tables, and lists the tables that
contain MAC addresses, the likely client tables, at the top of the report. MACs are
redacted in values and inside OID indexes (where many vendors put the client's MAC), and
the community or keys you type are removed. Use a read-only community.

A vendor walk contains the AP's whole configuration, which can include e-mail settings,
password hashes or even a Wi-Fi passphrase. So text and binary values are shown only as
their length (`STRING(9 chars)`), except in the likely client tables; numbers, MACs and
the table layout are kept, which is what a driver needs. `--snmp-full-values` shows all
values (still with the redaction above): use it only if asked, and read the result
carefully. `--snmp-root <OID>` adds a subtree, `--snmp-max` limits each walk (default
20000 values).

Not every AP lists clients over SNMP: the DAP-2610, for example, only reports client
counts and its MAC filter lists there, which is why its driver uses SSH.

Example: [`docs/ap-reports/dlink-dap-2610-v2.06.txt`](docs/ap-reports/dlink-dap-2610-v2.06.txt)
is the report the D-Link driver was checked against (DAP-2610, firmware v2.06,
`--profile dlink_dap --legacy-ssh`). It shows the command list, the client table for each
radio, and how redaction looks. Reports for supported models are kept in
[`docs/ap-reports/`](docs/ap-reports) as reference data for driver and parser work.

If your AP lists clients only in its web UI, say so in the issue.

## Roadmap

One entry per access point, whichever way it is read (see
[Two ways to read an access point](#two-ways-to-read-an-access-point)): where a vendor
offers both, a direct driver and a controller driver can exist side by side. Every item
below needs an owner of that hardware to send data and test a build.

| Next | How | What's needed |
|---|---|---|
| **UniFi APs without a controller in HA** | SSH to each AP, `mca-dump` (JSON with each radio's station table and dBm signal) | `collect.py --profile unifi` report |
| **SNMP drivers** | One SNMP base driver plus a small table map per vendor (MikroTik registration table first; Cisco, Aruba, Ruckus as reports arrive) | `collect.py --snmp` report |
| **OpenWrt** | SSH, `iw dev <radio> station dump` | `collect.py --command "iw dev"` and a station dump |
| **TP-Link Omada (controller)** | Like UniFi: through Home Assistant's [TP-Link Omada](https://www.home-assistant.io/integrations/tplink_omada/) integration, no extra login | that integration's **Download diagnostics** file (it lists the connected clients with the AP they are on, already anonymised) |
| **TP-Link Omada EAP (direct)** | SSH or SNMP to each EAP, if the EAP lists its clients | `collect.py` report (SSH or `--snmp`) |
| **Other controllers** (Cisco WLC, Aruba Instant) | Through Home Assistant's integration where one exists, else the controller's API | interest and a test setup |

Also planned: Home Assistant-level tests, and splitting `ap_drivers/` into a standalone
library once there is more than one vendor.

## Development

The access point code lives in
[`ap_drivers/`](custom_components/wifi_association_presence/ap_drivers) and has no Home
Assistant imports, so it can later become a standalone library.

- Test an AP from the command line: `python3 scripts/probe.py --host <ip> --username admin`
- Tests: `python3 -m pytest tests` (needs `pytest` and `asyncssh`). The presence rules
  (merging AP reads, roaming, grace period, visits, retention, storage) live in
  [`presence.py`](custom_components/wifi_association_presence/presence.py) without Home
  Assistant imports and are tested directly; CI runs the tests, ruff, hassfest and the
  HACS validation on every push.
- The icon's source is [`docs/images/icon.svg`](docs/images/icon.svg); the PNGs in
  `custom_components/wifi_association_presence/brand/` (256 and 512 px) are rendered from
  it. Home Assistant 2026.3+ uses them in place of the brands repository.

### Releasing

Bump `version` in `manifest.json`, add a `## <version>` section at the top of
`CHANGELOG.md`, and push to `main`. The [release workflow](.github/workflows/release.yml)
runs the tests and hassfest and, if they pass, creates the tag `v<version>` and a GitHub
release with that changelog section as its notes. HACS offers the new tag as an update.

### Adding a driver

Create a module in `ap_drivers/` with a class deriving from `AccessPointDriver`, decorate
it with `@register`, and set:

- `TYPE` (stable ID stored in configuration; never change it), `NAME` (shown in the type
  list), `MANUFACTURER`, `FIELDS` (the settings it needs) and `SIGNAL_UNIT` (`"%"` or
  `"dBm"`, the scale of `AssociatedClient.signal`);
- `async_get_associated_clients()`, returning `AssociatedClient` objects and raising
  `AccessPointAuthError` / `AccessPointError` on failure;
- optionally `async_poll()` to return device details (`AccessPointInfo`) from the same
  session;
- optionally `UNIQUE_FIELD` (the setting that identifies the AP, default `"host"`),
  `POLL_TIMEOUT` (seconds allowed for one poll, default 45) and, for an AP that another
  integration already registers as a device, `OWN_DEVICE = False` with `device_mac()`
  returning its MAC: that device's area is used and no device or sensors are created;
- for a driver that reads through another Home Assistant integration instead of
  talking to the AP (like UniFi), `SOURCE = "<name>"`: the integration passes that data
  source as the constructor's second argument, and the access point setting
  (`UNIQUE_FIELD`) becomes a list of what the source knows. Register the source once in
  [`sources.py`](custom_components/wifi_association_presence/sources.py) (how to create
  it, the options, the "nothing left to add" message); the driver module itself stays free
  of Home Assistant imports;
- for SSH consoles, an `SshPolicy` (algorithms and login method) for that model.

Treat every reply as untrusted: return `None` for anything you can't parse rather than
passing error text through as a value. Import the module at the bottom of
`ap_drivers/__init__.py`. New setting names need labels in `strings.json` and
`translations/en.json`.

## License

GPL-3.0
