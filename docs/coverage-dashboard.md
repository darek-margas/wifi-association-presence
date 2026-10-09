# Wi-Fi coverage dashboard (beta)

From version 0.7.0b3 the integration also tells you **how well your Wi-Fi covers the
home**, from the same access point polls it uses for presence. You don't need a floor plan,
an app on the phone or extra hardware.

It answers questions like:

- Which access point has clients on a weak signal? That room needs a better-placed AP.
- Which device keeps dropping off the Wi-Fi for a moment? It may be in a coverage hole,
  or it may be its power saving.
- Which device stays on a far access point too long before it switches (a "sticky"
  client)?

## The sensors

### Per access point

You get these for access points that have their own device in the integration (D-Link,
OpenWrt, MikroTik). UniFi access points belong to the UniFi integration, so they don't get
them.

| Sensor | What it shows |
|---|---|
| **Average client signal** | Average signal quality (0-100 %) of the clients connected to it right now. Empty when nobody is connected. |
| **Weak clients** | How many of its clients are below 50 % (about -75 dBm). Those connections are slow and unreliable. |

### Per tracked device

| Sensor | What it shows |
|---|---|
| **Roams today** | How many times it moved to another access point. Moving around the house makes some roams normal. Many roams while it sits still mean two APs overlap with similar signal, so it flips between them. |
| **Short drops today** | How many times it disappeared from every access point and came back within the grace period ("consider home"). Presence hides these gaps, so the tracker stays *home*. Frequent drops point to a coverage hole, or to aggressive Wi-Fi power saving on the device. |
| **Late roams today** | How many roams improved its signal by 30 points (about 15 dB) or more. It stayed on a far, weak AP although a much better one was in reach. A few are normal. Many point to a sticky client, or an AP whose transmit power is set too high so it looks closer than it is. |

### Good to know

- The counts start again from 0 **at midnight** (local time) **and when Home Assistant
  restarts**. They are kept in memory only.
- Signal is what the **access point** hears from the device, and only from the AP it is
  connected to. Access points don't report how the other APs hear it, so this can't draw
  a map. It shows where clients struggle, not where the walls are.
- If an access point can't be read in one poll, its clients aren't counted as dropped.
- A device that's away longer than the grace period has **left**, so it isn't a drop.
  When it comes back at another access point, that isn't a roam either.
- The sensors are recorded like any other. You get history graphs and long-term
  statistics. The per-device counts use the *total increasing* state class, so a midnight
  reset is understood as a reset.

## Dashboard

Replace the example entity ids with yours. They come from your device names: look under
**Settings → Devices & services → Wi-Fi Association Presence**, open a device, and click a
sensor.

Add a new dashboard view, open the **⋮ menu → Edit dashboard → ⋮ → Raw configuration
editor**, and paste this as a view:

```yaml
title: Wi-Fi coverage
path: wifi-coverage
icon: mdi:wifi-check
cards:
  - type: entities
    title: Access points now
    entities:
      - entity: sensor.hall_ap_average_client_signal
        name: Hall – average signal
      - entity: sensor.hall_ap_weak_clients
        name: Hall – weak clients
      - entity: sensor.studio_ap_average_client_signal
        name: Studio – average signal
      - entity: sensor.studio_ap_weak_clients
        name: Studio – weak clients

  - type: statistics-graph
    title: Average client signal per access point
    chart_type: line
    period: hour
    days_to_show: 7
    stat_types:
      - mean
      - min
    entities:
      - sensor.hall_ap_average_client_signal
      - sensor.studio_ap_average_client_signal

  - type: glance
    title: Today
    columns: 3
    entities:
      - entity: sensor.phone_roams_today
        name: Phone roams
      - entity: sensor.phone_drops_today
        name: Phone drops
      - entity: sensor.phone_late_roams_today
        name: Phone late
      - entity: sensor.laptop_roams_today
        name: Laptop roams
      - entity: sensor.laptop_drops_today
        name: Laptop drops
      - entity: sensor.laptop_late_roams_today
        name: Laptop late

  - type: history-graph
    title: Where the phone was and how strong
    hours_to_show: 24
    entities:
      - entity: sensor.phone_access_point
      - entity: sensor.phone_signal
```

The *history graph* of a device's **Access point** and **Signal** shows each roam: the
signal before and after it, and how long the device stayed on a weak access point.

### All devices automatically (optional)

With the [auto-entities](https://github.com/thomasloven/lovelace-auto-entities) card
(from HACS) you don't have to list entities, and new access points and devices show up by
themselves:

```yaml
type: vertical-stack
cards:
  - type: custom:auto-entities
    card:
      type: entities
      title: Average client signal
    filter:
      include:
        - entity_id: sensor.*_average_client_signal
    sort:
      method: state
      numeric: true
  - type: custom:auto-entities
    card:
      type: entities
      title: Short drops today
    filter:
      include:
        - entity_id: sensor.*_drops_today
          state: "> 0"
    sort:
      method: state
      numeric: true
      reverse: true
    show_empty: false
  - type: custom:auto-entities
    card:
      type: entities
      title: Late roams today
    filter:
      include:
        - entity_id: sensor.*_late_roams_today
          state: "> 0"
    sort:
      method: state
      numeric: true
      reverse: true
    show_empty: false
```

## Reading it

- **An access point whose average stays low, or that always has weak clients:** move it,
  add one nearby, or check what blocks it (a metal cabinet, a floor heating loop, a
  mirror).
- **One device with many short drops, while others in the same room have none:** that's
  the device. Check its Wi-Fi power saving, or its distance from the AP when it sleeps.
- **Many devices with short drops on the same AP:** look at that AP. Check its log,
  channel and interference. A power problem can also cause this (does its **Last boot**
  change?).
- **Many late roams:** lower the transmit power of the strongest APs so their cells
  overlap less, or turn on the AP's band steering or minimum-signal options where it has
  them.

This is a **beta**. Thresholds and names may still change. Tell us what you see, or what
you'd like it to show, in an
[issue](https://github.com/darek-margas/wifi-association-presence/issues).
