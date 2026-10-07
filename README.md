# Wi-Fi Association Presence

Home Assistant presence detection from Wi-Fi access point **association tables**.

A device is *home* while it is **associated** (authenticated and joined) to one of your
access points, as reported by the AP itself. That is different from, and more reliable
for phones than, a MAC seen in an ARP table or a switch's MAC table: a phone stays
associated while it is idle, even when it sends no traffic for minutes.

> Status: early development (0.1.0). Not yet installable through HACS while the repo is private.

## How it works

- One integration entry, with **access points** and **tracked devices** added from the
  integration page (config subentries).
- Every 60 s all access points are read in parallel. A tracked device is `home` while
  its MAC is associated to any AP, and `not_home` once it has been missing for 3 minutes
  (so a missed poll or roaming between APs doesn't flip it).
- Tracker attributes: `access_point`, `ssid`, `band`, `rssi`, `last_seen`.

## Supported access points

Access point types are pluggable drivers in
[`ap_drivers/`](custom_components/wifi_association_presence/ap_drivers) (no Home Assistant
code, so it can later become a standalone library).

| Type | Models | Method |
|---|---|---|
| `dlink_dap_ssh` | D-Link DAP series (tested: DAP-2610; expected: DAP-3662) | SSH console: `config wlan 0/1` + `get clientinfo` |

D-Link DAP notes:
- Enable SSH under **Maintenance → Administration → Console Settings**.
- Only password login is supported by the AP (no SSH keys). Restrict management access
  with **Limit Administrator** to your Home Assistant host.
- The driver allows the old SSH algorithms these units require (`diffie-hellman-group1-sha1`,
  `ssh-rsa`, CBC ciphers) and does not verify the host key.

### Adding a driver

Create a module in `ap_drivers/` with a class deriving from `AccessPointDriver`, decorate it
with `@register`, set `TYPE`, `NAME` and `FIELDS`, and implement
`async_get_associated_clients()`. Import the module at the bottom of
`ap_drivers/__init__.py`. New field keys need labels in `strings.json` /
`translations/en.json`.

## Testing an access point without Home Assistant

```bash
pip install asyncssh
python3 scripts/probe.py --type dlink_dap_ssh --host 192.168.1.231 --username admin
```

## Manual installation

Copy `custom_components/wifi_association_presence` to `/config/custom_components/` and
restart Home Assistant, then add **Wi-Fi Association Presence** under
**Settings → Devices & services**.

## License

GPL-3.0
