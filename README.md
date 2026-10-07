<img src="docs/images/icon.svg" alt="" width="96" align="right">

# Wi-Fi Association Presence

Home Assistant presence detection from Wi-Fi access point **association tables**.

A device is *home* while it is **associated** (authenticated and joined) to one of your
access points, as reported by the AP itself. That is different from, and for phones more
reliable than, a MAC seen in an ARP table or a switch's MAC table: a phone stays
associated while it is idle, even when it sends no traffic for minutes.

> **Status:** early development (0.2.x). Only **D-Link DAP** access points are supported
> so far, because those are what the author has. Tested on Home Assistant 2026.9 with a
> DAP-2610 and a DAP-3662. See [Limitations](#limitations) and
> [Help add your access point](#help-add-your-access-point).

## Features

- **Presence per device**, across all your access points. A tracked device is `home` while
  its MAC is associated to any AP and `not_home` once it has been missing for the
  **grace period** (default 3 minutes, adjustable), which covers roaming between APs,
  coverage gaps and an AP that is briefly unreachable.
- **A device per tracked phone/laptop**, holding its `device_tracker` and three sensors:
  - *Access point*: where it is connected now.
  - *Signal*: signal strength on the AP's scale (percent on D-Link).
  - *Area*: the Home Assistant area of the access point it is connected to (assign an
    area to each AP device), with `area_id` as an attribute. Useful for room-level
    automations; empty while away or when the AP has no area.
  - Tracker attributes: `access_point`, `area`, `ssid`, `band`, `rssi`, `last_seen`.
- **A device per access point**: firmware, hardware revision, optional model, a link to
  its web UI, and sensors:
  - *Clients 2.4 GHz*, *Clients 5 GHz*, *Clients* (total).
  - Diagnostic: *CPU*, *Memory*, *Last boot*, *Location*.
- **SSID names** resolved from the AP, not just "SSID index 3".
- **Set up entirely in the UI**: access points and tracked devices are added, edited and
  removed as entries of the integration.
- **Fault tolerant**: an access point that is unreachable, slow, or answers with something
  unexpected only affects itself. Its sensors become unavailable and its clients age out
  after the grace period; the other APs keep updating.

## Screenshots

The integration page: one hub, with access points and tracked devices as entries.

![Integration page](docs/images/integration-page.png)

An access point device: firmware, hardware, client counts per band, CPU, memory, last boot.

![Access point device](docs/images/access-point-device.png)

A tracked device: its tracker, the access point it is connected to, and the signal.

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

Tracked devices are independent of access points. Removing, replacing or changing the
type of an access point doesn't touch them.

## Installation

Not in HACS yet while the repository is private. Copy
`custom_components/wifi_association_presence` to `/config/custom_components/` and restart
Home Assistant. Requires Home Assistant **2026.9** or newer.

## Setup

1. **Settings → Devices & services → Add integration → Wi-Fi Association Presence.**
2. On the integration page, **Add access point**: choose the type, then its settings.
   The settings are tested by reading the AP once. Leave the name empty to use the name
   the AP reports about itself (D-Link: its system name, e.g. "Studio").
3. **Add tracked device**: pick one of the currently associated devices (labelled with AP,
   band, SSID and signal) or type a MAC address, and give it a name.
4. Optional: **Configure** on the hub sets the grace period.

Access points and tracked devices can be edited from their **⋮** menu (**Reconfigure**).
Leaving an access point's password empty keeps the current one.

## Supported access points

| Type | Models | Method |
|---|---|---|
| D-Link DAP (SSH console) | **Tested:** DAP-2610 (fw v2.06, [report](docs/ap-reports/dlink-dap-2610-v2.06.txt)), DAP-3662. **Likely:** other DAP models with the same CLI | SSH: `config wlan 0/1` + `get clientinfo` |

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
  on owners contributing data (see below); the driver interface is designed for it.
- **What the D-Link CLI provides is about all there is.** On the tested firmware it gives
  the client list per radio (MAC, SSID, signal, connected time), SSID names, system
  name, location, firmware, hardware revision, uptime, CPU and memory. It does **not**
  report:
  - the model (enter it in the access point's settings if you want it on the device);
  - the AP's MAC address (`get macaddress` fails on this firmware);
  - clients' IP addresses or host names, so trackers have no `ip`/`host_name`, and Home
    Assistant features that rely on a tracker's IP aren't available.
- Signal strength is the AP's own scale (D-Link: percent), not dBm, and isn't comparable
  across vendors.

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
  in HACS while private.
- Parser tests exist but no CI yet.

## Help add your access point

You don't need to write code to get an access point supported. Run the collector against
it and attach the report to a
[New access point model](../../issues/new?template=new_access_point.yml) issue:

```bash
pip install asyncssh
python3 scripts/collect.py --host <AP IP> --username <user>
```

- It runs **read-only commands only** (anything that could change settings is refused)
  and **redacts** MAC addresses (vendor prefix kept), IP addresses and secret-looking
  settings. Review the report before sharing it.
- `--legacy-ssh` allows old SSH algorithms if the connection fails.
- `--command "<cmd>"` (repeatable) adds the command your AP uses to list clients.
- `--profile dlink_dap` uses the D-Link command list; `--list-profiles` shows all.

Example: [`docs/ap-reports/dlink-dap-2610-v2.06.txt`](docs/ap-reports/dlink-dap-2610-v2.06.txt)
is the report the D-Link driver was checked against (DAP-2610, firmware v2.06,
`--profile dlink_dap --legacy-ssh`). It shows the command list, the client table for each
radio, and how redaction looks. Reports for supported models are kept in
[`docs/ap-reports/`](docs/ap-reports) as reference data for driver and parser work.

Currently the collector only supports SSH consoles. If your AP lists clients only in its
web UI or over SNMP, say so in the issue.

## Development

The access point code lives in
[`ap_drivers/`](custom_components/wifi_association_presence/ap_drivers) and has no Home
Assistant imports, so it can later become a standalone library.

- Test an AP from the command line: `python3 scripts/probe.py --host <ip> --username admin`
- Parser tests: `python3 -m pytest tests`
- The icon's source is [`docs/images/icon.svg`](docs/images/icon.svg); the PNGs in
  `custom_components/wifi_association_presence/brand/` (256 and 512 px) are rendered from
  it. Home Assistant 2026.3+ uses them in place of the brands repository.

### Adding a driver

Create a module in `ap_drivers/` with a class deriving from `AccessPointDriver`, decorate
it with `@register`, and set:

- `TYPE` (stable ID stored in configuration; never change it), `NAME` (shown in the type
  list), `MANUFACTURER`, and `FIELDS` (the settings it needs);
- `async_get_associated_clients()`, returning `AssociatedClient` objects and raising
  `AccessPointAuthError` / `AccessPointError` on failure;
- optionally `async_poll()` to return device details (`AccessPointInfo`) from the same
  session;
- for SSH consoles, an `SshPolicy` (algorithms and login method) for that model.

Treat every reply as untrusted: return `None` for anything you can't parse rather than
passing error text through as a value. Import the module at the bottom of
`ap_drivers/__init__.py`. New setting names need labels in `strings.json` and
`translations/en.json`.

## License

GPL-3.0
