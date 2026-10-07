# Wi-Fi Association Presence

Home Assistant presence detection from Wi-Fi access point **association tables**.

A device is *home* while it is **associated** (authenticated and joined) to one of your
access points, as reported by the AP itself. That is different from, and for phones more
reliable than, a MAC seen in an ARP table or a switch's MAC table: a phone stays
associated while it is idle, even when it sends no traffic for minutes.

> Status: early development (0.1.x). Tested on Home Assistant 2026.9 with D-Link DAP-2610
> and DAP-3662.

## Features

- **Presence per device**, across all your access points and AP types. A tracked device is
  `home` while its MAC is associated to any AP and `not_home` once it has been missing for
  the **grace period** (default 3 minutes, adjustable), which covers roaming between APs,
  coverage gaps and an AP that is briefly unreachable.
- **A device per tracked phone/laptop** with its `device_tracker` and sensors:
  - *Access point*: where it is connected now.
  - *Signal*: signal strength on the AP's scale (percent on D-Link).
  - Tracker attributes: `access_point`, `ssid`, `band`, `rssi`, `last_seen`.
- **A device per access point** with firmware, hardware revision, optional model, a link to
  its web UI, and sensors:
  - *Clients 2.4 GHz*, *Clients 5 GHz*, *Clients* (total).
  - Diagnostic: *CPU*, *Memory*, *Last boot*, *Location*.
- **SSID names** resolved from the AP (not just "SSID index 3").
- Everything is set up in the UI: access points and tracked devices are added, edited
  and removed as entries of the integration.

## How presence is decided

Every 60 seconds all access points are read in parallel. A device seen on two APs in the
same poll (while roaming) is attributed to the one with the stronger signal. If an AP
can't be read, its clients are not marked away immediately: their last sighting simply
ages out after the grace period, and the AP's sensors become unavailable.

Tracked devices are independent of access points. Removing, replacing or changing the
type of an access point doesn't touch them, and with no access point configured at all
they report `unknown` instead of `not_home`.

## Installation

Not yet in HACS while the repository is private. Copy
`custom_components/wifi_association_presence` to `/config/custom_components/` and restart
Home Assistant.

## Setup

1. **Settings → Devices & services → Add integration → Wi-Fi Association Presence.**
2. On the integration page, **Add access point**: choose the type, then its settings.
   The settings are tested by reading the AP's client list once.
3. **Add tracked device**: pick one of the currently associated devices (labelled with AP,
   band, SSID and signal) or type a MAC address, and give it a name. For phones with
   private Wi-Fi addresses, use the address shown in the phone's settings for that network.
4. Optional: **Configure** on the hub sets the grace period.

Access points and tracked devices can be edited from their **⋮** menu (**Reconfigure**).
Leaving an access point's password empty keeps the current one.

## Supported access points

| Type | Models | Method |
|---|---|---|
| D-Link DAP (SSH console) | DAP-2610, DAP-3662 (other DAP models with the same CLI likely work) | SSH: `config wlan 0/1` + `get clientinfo` |

### D-Link DAP notes

- Enable SSH under **Maintenance → Administration → Console Settings** (protocol SSH).
- The AP only supports password login (no SSH keys). Limit management access with
  **Limit Administrator** to your Home Assistant host.
- The driver allows the old SSH algorithms these units require
  (`diffie-hellman-group14-sha1`, `diffie-hellman-group1-sha1`, `ssh-rsa`, CBC ciphers),
  logs in by password only, and does not verify the host key.
- The CLI doesn't report the model; enter it in the access point's settings if you want
  it shown on the device.
- `get clientinfo` lists the radio selected with `config wlan 0` (2.4 GHz) or
  `config wlan 1` (5 GHz); `set band` does not change it.

## Help add your access point

You don't need to write code to get an access point supported. Run the collector against
it and attach the report to a
[New access point model](../../issues/new?template=new_access_point.yml) issue:

```bash
pip install asyncssh
python3 scripts/collect.py --host <AP IP> --username <user>
```

- It runs **read-only commands only** (anything that could change settings is refused),
  and **redacts** MAC addresses (vendor prefix kept), IP addresses and secret-looking
  settings. Review the report before sharing it.
- `--legacy-ssh` allows old SSH algorithms if the connection fails.
- `--command "<cmd>"` (repeatable) adds the command your AP uses to list clients.
- `--profile dlink_dap` uses the D-Link command list; `--list-profiles` shows all.

## Development

The access point code lives in
[`ap_drivers/`](custom_components/wifi_association_presence/ap_drivers) and has no Home
Assistant imports, so it can later become a standalone library.

- Test an AP from the command line: `python3 scripts/probe.py --host <ip> --username admin`
- Parser tests: `python3 -m pytest tests`

### Adding a driver

Create a module in `ap_drivers/` with a class deriving from `AccessPointDriver`, decorate
it with `@register`, and set:

- `TYPE` (stable ID stored in configuration), `NAME` (shown in the type list),
  `MANUFACTURER`, and `FIELDS` (the settings it needs);
- `async_get_associated_clients()`, returning `AssociatedClient` objects;
- optionally `async_poll()` to return device details (`AccessPointInfo`) from the same
  session;
- for SSH consoles, an `SshPolicy` (algorithms and login method) for that model.

Import the module at the bottom of `ap_drivers/__init__.py`. New setting names need labels
in `strings.json` and `translations/en.json`.

## License

GPL-3.0
