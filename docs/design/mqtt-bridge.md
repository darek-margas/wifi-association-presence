# Design note: an MQTT bridge (Wi-Fi presence without Home Assistant)

Status: **proposal, mostly agreed** (2026-10-20): name, Node-RED friendly topics and
the configuration split are decided (see *Decisions* at the end); topics by id or by MAC
is still open. Written 2026-10-20. The first step is
done: the presence rules live in the library since wifi-ap-associations 0.12.0
(`presence`, `coverage`, `tracker`), so the bridge and the integration decide home and
away the same way.

## Goal

A small service, shipped as a container, that reads the same access points the
integration reads and publishes over MQTT who is home, on which access point, and the
access points' own state. For Node-RED, openHAB, ioBroker, Domoticz, or Home Assistant
installations that would rather take MQTT than install a custom integration.

Not in scope, at least at first: a web UI, editing the configuration at runtime,
anything that writes to an access point, and keeping history (that's the consumer's
job; MQTT carries the current state and the events).

## How it works

```
access points ──(SSH / SNMP / controller API)──> bridge ──> MQTT broker ──> consumers
                                                   │
                                     /data/state.json (visits across restarts)
```

One asyncio program, built only on the library:

1. Every `poll_interval` (default 60 s) it polls all access points with `poll_all()` and
   feeds the round to a `PresenceTracker`.
2. For types that can watch (MikroTik, OpenWrt, UniFi), `watch_forever()` keeps the
   change feed open; a change asks for a poll at once, at most one per 5 s (as the
   integration's debounce does).
3. After each round it compares the tracker's snapshot with the last one published and
   publishes what changed: device states, access point states, events.
4. Every 10 minutes, and when the container stops (SIGTERM), it writes
   `tracker.to_storage()` to `/data/state.json` (clean on stop), so arrival times and
   today's coverage counts survive a restart, exactly as in Home Assistant.

A total outage (no access point readable) beyond the grace period makes every tracked
device's state `unknown`, never `not_home`, as the integration makes its trackers
unavailable.

## Configuration

Two parts, both from the start:

- **Environment variables for the static basics:** the broker (`MQTT_HOST`, `MQTT_PORT`,
  `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TOPIC_PREFIX`), the presence settings
  (`GRACE_PERIOD`, `POLL_INTERVAL`, `INSTANT_ARRIVALS`), `LOG_LEVEL`, and credentials.
  Any variable `X` may instead come from a file named by `X_FILE` (Docker secrets).
- **A YAML file for the details**, mounted at `/config/config.yaml`: access points,
  controllers and devices. `${NAME}` in it is replaced by that environment variable, so
  passwords stay in the environment. A setting given in both places takes the
  environment's value, and the log says so once at start.

### Strict parsing: exact errors, then exit

The bridge reads and checks the whole configuration before connecting to anything, and
on any problem logs every error it found and **exits with status 1** (so Docker shows
the container as failed and `restart: unless-stopped` doesn't hide it in a loop of
half-working starts; a broken config file never runs with defaults). Each message
names where and what:

```
config error: /config/config.yaml line 14, column 5: mapping values are not allowed here (YAML syntax)
config error: /config/config.yaml line 22: access_points[1] (upstairs): unknown key "caps" (did you mean "cap"?)
config error: /config/config.yaml line 22: access_points[1] (upstairs): missing required key "cap" for type mikrotik_capsman_ssh
config error: /config/config.yaml line 30: access_points[3]: type "openwrt" is not a known type (known: dlink_dap_ssh, openwrt_ssh, ...)
config error: /config/config.yaml line 41: devices[0] (alice_phone): mac "5C:AD:BA:00:01" is not a MAC address
config error: /config/config.yaml line 9: ${HALLWAY_PASSWORD} is not set in the environment
config error: environment POLL_INTERVAL="sixty": not a number of seconds
config error: devices: id "alice_phone" used twice (lines 41 and 47)
3 errors; nothing was started.
```

- YAML syntax errors carry the parser's line and column. Everything else carries the
  line of the entry, its path (`access_points[1]`) and its id when it has one.
- Unknown keys are errors, with the closest valid key suggested; values are checked for
  type, range (e.g. `poll_interval` 10 to 3600 s), the driver's own choices, MAC format
  and unique ids.
- An access point's keys are checked against its driver's `FIELDS`, so they are exactly
  the integration's setup form.
- Passwords never appear in a message, even when the value is what's wrong.
- `wifi-presence-mqtt --check-config` (also as `docker run ... --check-config`) runs only
  this check and exits 0 or 1, for checking a change before restarting.

Errors that are not about the configuration (an access point unreachable, a wrong
password on an AP, the broker down at start) don't exit: they are logged and retried,
as in Home Assistant, with the access point shown `offline`.

```yaml
mqtt:
  host: mosquitto
  port: 1883
  username: wifi-presence
  password: ${MQTT_PASSWORD}
  topic_prefix: wifi-presence      # default
  # tls: true, ca_file: /config/ca.pem   (optional)

presence:
  grace_period: 180                # seconds, as "consider home" in the integration
  poll_interval: 60
  instant_arrivals: true

access_points:
  - id: hallway                    # used in topics; letters, digits, - and _
    name: Hallway                  # optional, else what the AP reports
    area: Hallway                  # optional, published with the device's state
    type: openwrt_ssh              # any library driver type (wifi-ap-probe --list-types)
    host: 192.168.1.2
    username: root
    password: ${HALLWAY_PASSWORD}
  - id: upstairs
    area: Bedroom
    type: mikrotik_capsman_ssh
    host: 192.168.1.1
    username: presence
    password: ${MIKROTIK_PASSWORD}
    cap: ap_upstairs
  - id: office
    type: unifi_network            # needs a controller, below
    controller: unifi
    ap_mac: 78:8a:20:00:00:10

controllers:                       # only for UniFi and TP-Link Omada
  unifi:
    type: unifi
    host: 192.168.1.10
    username: presence             # a read-only local user on the controller
    password: ${UNIFI_PASSWORD}
    site: default
    verify_ssl: false

devices:
  - id: alice_phone
    name: Alice's phone
    mac: 5C:AD:BA:00:00:01
```

The access point keys are the driver's own `FIELDS`, the same as the integration's
setup form; the bridge checks them at start and stops with a clear message naming the
entry and the key. Unknown keys are an error too, so a typo doesn't go unnoticed.

Only listed devices are published by default. `publish_all_clients: true` also
publishes every client seen (MAC, AP, signal) on one topic, for building the device list;
off by default, since it publishes the neighbours' phones too.


## MQTT topics

All under `topic_prefix` (default `wifi-presence`). States are retained, QoS 1; events
are not retained.

| Topic | Payload | Retained |
|---|---|---|
| `wifi-presence/status` | `online` / `offline` (offline is the last will) | yes |
| `wifi-presence/device/<id>/state` | `home`, `not_home` or `unknown` | yes |
| `wifi-presence/device/<id>/attributes` | JSON: `mac`, `access_point`, `area`, `ssid`, `band`, `signal`, `signal_unit`, `quality` (0-100), `last_seen`, `roams_today`, `short_drops_today`, `late_roams_today` | yes |
| `wifi-presence/device/<id>/home_since` | ISO time the current visit started; empty while away | yes |
| `wifi-presence/device/<id>/away_since` | ISO time the device was declared away; empty while home | yes |
| `wifi-presence/device/<id>/access_point` | id of the access point it is on; empty while away | yes |
| `wifi-presence/ap/<id>/state` | `online` / `offline` (readable or not) | yes |
| `wifi-presence/ap/<id>/attributes` | JSON: `name`, `model`, `firmware`, `uptime_seconds`, `cpu_percent`, `memory_percent`, `clients` (per band), `average_signal_quality`, `weak_clients`, `error` (when offline) | yes |
| `wifi-presence/event` | JSON: `{"event": "arrived" / "left" / "roamed", "device": id, "access_point": id, "from": id, "at": ...}` | no |
| `wifi-presence/clients` | JSON list of every client seen (only with `publish_all_clients`) | yes |

`left` is published when the grace period runs out, not when the client disconnects, so
a phone that sleeps or roams never sends a false departure, the same rule as the
integration.

The times and the access point have their own topics as well as being in the
attributes, so a Node-RED flow can use a plain `mqtt in` node without parsing JSON.

When a device or access point is removed from the configuration, the bridge clears its
retained topics at the next start (an empty retained message), so nothing stale stays
on the broker.

### Home Assistant MQTT discovery (optional)

`homeassistant_discovery: true` publishes discovery messages under `homeassistant/`, so
Home Assistant creates for each device a `device_tracker` (source type `router`) plus
access point and signal sensors, without installing this integration. Off by default:
most users of the bridge don't run Home Assistant, and those who do usually want the
integration itself.

## UniFi and TP-Link Omada without Home Assistant

Their drivers read through a source object (`UnifiSource`, `OmadaSource`), which the
integration fills from Home Assistant's own integrations. The bridge fills them itself,
from the same client libraries those integrations use, with the controller login from
`controllers:`:

- **UniFi:** `aiounifi`, logged in with a read-only local controller user. Its
  websocket carries the connect and roam events for instant arrivals, as in Home
  Assistant.
- **TP-Link Omada:** `tplink-omada-client`, with the controller URL, user, password and
  site. Polling only (no events), and experimental like the driver.

Both are optional extras in the image, so the base needs only asyncssh (and pysnmp for
SNMP). One controller read per poll round serves all its access points, as in the
integration's sources.

## Container

- Built by a GitHub Actions workflow on each version tag, for `linux/amd64` and
  `linux/arm64` (Raspberry Pi), published to `ghcr.io/darek-margas/wifi-presence-mqtt`
  with tags `latest`, the version (`0.1.0`), and `beta` for prereleases.
- `python:3.13-slim`, runs as a non-root user, `/config` read-only, `/data` writable.
- Logs to stdout; `LOG_LEVEL=debug` shows the library's debug lines (feed lines,
  command times), as Home Assistant's debug logging does.
- A health check: healthy while the MQTT connection is up and the last poll round
  finished within three poll intervals.
- Needs only outgoing connections: to the access points (SSH 22, SNMP 161, controller
  HTTPS) and to the broker. No ports to publish.

```yaml
services:
  wifi-presence:
    image: ghcr.io/darek-margas/wifi-presence-mqtt:latest
    restart: unless-stopped
    volumes:
      - ./config.yaml:/config/config.yaml:ro
      - ./data:/data
    env_file: .env                 # MQTT_PASSWORD=..., HALLWAY_PASSWORD=...
```

## Security

The same as the library: SSH logins with a password, host keys not verified, read-only
commands only. Passwords come from environment variables or Docker secrets, never from
the published image; the config file may hold them in plain text if the user prefers,
with a warning in the README. The bridge publishes MAC addresses of tracked devices only
(unless `publish_all_clients`); topics use the configured ids, not MACs.

## Testing

- Unit tests: configuration parsing and validation (each error message), environment and
  secret substitution, the payloads for each state change, the "unknown" state after a
  long total outage, saving and restoring.
- An end-to-end test in CI: the bridge against a fake access point driver and a real
  Mosquitto broker (a service container), checking the retained topics after an arrival,
  a roam and a departure.
- Live: the OpenWrt VMs (`docs/openwrt-vm-test.md`) and a local Mosquitto, including
  instant arrivals and a restart in the middle of a visit.

## Plan

1. **0.1.0b1:** SSH and SNMP access points (OpenWrt, MikroTik, CAPsMAN, D-Link, Cisco),
   presence and coverage, the topics above, state file, container on ghcr.io, compose
   example.
2. **0.1.0b2:** UniFi and Omada controller logins; UniFi instant arrivals.
3. **0.1.0b3:** Home Assistant MQTT discovery.
4. **0.1.0:** after testers on Node-RED and one other platform confirm it.

## Decisions (2026-10-20)

1. Repository and image: `wifi-presence-mqtt`.
2. Times (`home_since`, `away_since`) and the current access point get their own
   retained topics, for Node-RED, besides the attributes JSON.
3. Configuration from both from the start: environment for the static basics and
   credentials, YAML for access points, controllers and devices; strict parsing that
   logs every exact fault and exits.

## Open

- **Topics by configured id (proposed) or by MAC.** By id, a device keeps its topic when
  its MAC changes (new phone, private / rotating Wi-Fi addresses, now the default on
  phones): only the config line changes, flows don't. Ids are readable in flows and keep
  MACs out of the topic tree (the MAC is still in the attributes). By MAC, nothing needs
  naming, but every MAC change is a new topic, old retained topics pile up, and flows
  must be edited each time.
