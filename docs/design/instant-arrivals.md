# Design note: instant arrivals (events as a poll trigger)

Status: **planned, not built.** Written 2026-10-10 from tests on a real network.

Today an arrival shows within one poll (up to 60 s). Most access points can also tell
someone that a client just joined. The idea: use such an event only to **poll that access
point now**, not as the source of truth.

- The poll still decides who is there, as today. Nothing in the message is trusted, so a
  lost, late or forged message costs at most one extra poll.
- Arrivals show in seconds instead of up to a minute.
- Departures don't change: they wait for the grace period. Phones disconnect and rejoin
  constantly (power saving, roaming), so a "client left" event must never mark anyone
  away by itself.
- Triggered polls are rate-limited per access point (at most one every few seconds), so a
  burst of events can't turn into a poll storm.

## Event sources, cheapest first

| Source | Access points | Setup for users | Notes |
|---|---|---|---|
| UniFi integration events | UniFi | none | Home Assistant's UniFi integration already receives the controller's connect and roam events over its websocket (its own trackers use them). |
| hostapd ubus events | OpenWrt | none | One long-lived SSH session subscribed to hostapd's association events on ubus. |
| **RADIUS accounting** | any AP on 802.1X (WPA-Enterprise), or set up to send accounting for PSK too | depends, see below | Standard across vendors, authenticated with a shared secret. Details below. |
| Syslog | D-Link DAP (documented: log type *Wireless Activity* includes associate / disassociate), MikroTik | point the AP's log server at Home Assistant | Unauthenticated UDP on port 514; needs a listener in Home Assistant. |
| SNMP traps | Cisco WLC (association traps in its wireless MIBs) | trap receiver | Mainly for a future Cisco driver. D-Link has a trap server setting, but which traps it sends isn't documented. |

## RADIUS accounting

With 802.1X the access point reports every session to an accounting server (UDP 1813):
**Accounting-Start** on joining, **Stop** on leaving (with a reason), optionally
**Interim-Update** while it lasts, and **Accounting-On** when the AP (re)starts.

### What a D-Link DAP sends (DAP-2610, checked against FreeRADIUS's detail files)

Example records, addresses and user names made up:

```text
	Acct-Status-Type = Start
	User-Name = "alice"
	NAS-IP-Address = 192.0.2.31
	NAS-Identifier = "02:00:5e:10:20:30"
	Called-Station-Id = "02-00-5E-10-20-38:Home"
	Calling-Station-Id = "5C-AD-BA-00-00-01"
	Acct-Session-Id = "75F634D4B3E9485C"
	Event-Timestamp = "Oct 10 2026 13:42:34 AEDT"
	Acct-Delay-Time = 0

	Acct-Status-Type = Stop
	...
	Acct-Session-Time = 32
	Acct-Terminate-Cause = User-Request
```

- `Calling-Station-Id`: the client's MAC (dash notation).
- `NAS-IP-Address`: the access point, which tells us which AP to poll.
- `NAS-Identifier`: **the AP's own MAC address**, which the D-Link CLI can't report.
- `Called-Station-Id`: the BSSID of the radio/SSID plus the SSID name. No band or signal.
- `User-Name`: the 802.1X login, i.e. a **person**, not a device.
- Reported immediately (`Acct-Delay-Time = 0`); the AP clock was a few seconds off.
- **Start and Stop only, no Interim-Updates** (one AP over months: about 42,700 Starts,
  42,700 Stops, 200 Accounting-On). So a lost Stop can't be detected from RADIUS alone,
  which is fine when the polls stay the source of truth.
- Very many short sessions (phones dropping and rejoining), which confirms that a Stop
  must not mean "away".

D-Link offers a primary and a backup accounting server. On most APs the backup is a
failover target, not a copy; that needs testing before relying on it.

### Ways to get the events into Home Assistant

1. **Follow the RADIUS server's accounting log over SSH (first step).** FreeRADIUS (e.g. the
   pfSense package) writes every message to detail files, one folder per AP:
   `/var/log/radacct/<AP address>/detail-…`. One SSH session follows the newest file of
   each folder (handling rotation) and sees each Start within a second. No change on the
   access points; needs an SSH user on the RADIUS server that can read those files, and
   network access from Home Assistant to it.
2. **Home Assistant as the accounting server, forwarding to the real one.** The APs send
   accounting to Home Assistant, which acknowledges and passes every message on (so the
   existing RADIUS server still gets its accounting; the AP's backup accounting server can
   point at it for when Home Assistant is down). Best as a **separate, general "RADIUS
   accounting" integration** that turns each message into a Home Assistant event, usable
   by automations and other integrations too, not only by this one.
3. **Read the RADIUS server's SQL accounting table** (`radacct`), if it writes to SQL.
   Robust, with history, but polling again.

### Plan

1. **Library:** a parser for FreeRADIUS detail records (vendor-neutral), with tests built
   from real records.
2. **Integration:** an optional "RADIUS accounting log" source (host, SSH user, path).
   A Start triggers an immediate, rate-limited poll of the AP with that `NAS-IP-Address`.
   Stop and Accounting-On are recorded for diagnostics and the coverage counters, never
   used to set presence.
3. **Later, tracking by RADIUS user name:** a tracked person defined by the 802.1X login
   instead of a MAC, which works with rotating private MAC addresses on 802.1X networks.
4. **Later, the general RADIUS accounting integration** (way 2 above), sharing the parser.
