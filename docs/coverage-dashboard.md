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

You don't need to look up or type any entity ids. Home Assistant writes the dashboard for
you with your own access points and devices:

1. Go to **Developer tools → Template**, clear the editor and paste the template below. Don't
   change anything in it.
2. The **Result** pane on the right now shows a complete dashboard, with your access points
   and devices filled in. Copy all of it.
3. Go to **Settings → Dashboards → Add dashboard → New dashboard from scratch**, and name it
   *Wi-Fi coverage*.
4. Open the new dashboard itself from the sidebar. (Clicking it in the Settings list only
   opens its settings: title, icon, admin only.) At the top right, click the **pencil**
   (on some versions it's under **⋮ → Edit dashboard**). Edit mode is on when the top bar
   changes colour and shows **Done**.
5. In edit mode, open the **⋮** menu at the top right and choose **Raw configuration
   editor**. Select everything in the editor, paste the result over it, click **Save**,
   close the editor and click **Done**.

When you add an access point or a tracked device later, do the same again: run the
template, then paste the new result over the old one.

```jinja
{% set ents = integration_entities('wifi_association_presence') | select('match', '^sensor[.]') | list %}
{% set avg = ents | select('search', '_average_client_signal') | list %}
{% set weak = ents | select('search', '_weak_clients') | list %}
{% set roams = ents | select('search', '_roams_today') | reject('search', '_late_roams_today') | list %}
{% set drops = ents | select('search', '_short_drops_today') | list %}
{% set late = ents | select('search', '_late_roams_today') | list %}
{% set signal = ents | select('search', '_signal(_[0-9]+)?$') | reject('search', '_average_client_signal') | list %}
{% macro short(e, suffix) %}{{ (state_attr(e, 'friendly_name') or e) | replace(' ' ~ suffix, '') }}{% endmacro %}
views:
  - title: Wi-Fi coverage
    path: wifi-coverage
    icon: mdi:wifi-check
    cards:
      - type: entities
        title: Access points – average client signal
        entities:
{%- for e in avg %}
          - entity: {{ e }}
            name: {{ short(e, 'Average client signal') | tojson }}
{%- endfor %}
      - type: entities
        title: Access points – weak clients
        entities:
{%- for e in weak %}
          - entity: {{ e }}
            name: {{ short(e, 'Weak clients') | tojson }}
{%- endfor %}
      - type: statistics-graph
        title: Average client signal, 7 days
        chart_type: line
        period: hour
        days_to_show: 7
        stat_types: [mean, min]
        entities:
{%- for e in avg %}
          - {{ e }}
{%- endfor %}
      - type: entities
        title: Roams today
        entities:
{%- for e in roams %}
          - entity: {{ e }}
            name: {{ short(e, 'Roams today') | tojson }}
{%- endfor %}
      - type: entities
        title: Short drops today
        entities:
{%- for e in drops %}
          - entity: {{ e }}
            name: {{ short(e, 'Short drops today') | tojson }}
{%- endfor %}
      - type: entities
        title: Late roams today
        entities:
{%- for e in late %}
          - entity: {{ e }}
            name: {{ short(e, 'Late roams today') | tojson }}
{%- endfor %}
      - type: history-graph
        title: Device signal, 24 h
        hours_to_show: 24
        entities:
{%- for e in signal %}
          - entity: {{ e }}
            name: {{ short(e, 'Signal') | tojson }}
{%- endfor %}
```

What you get:

- **Access points:** the average client signal and the number of weak clients of each,
  and a 7-day graph of the average and the lowest hourly value. The graph fills in over
  time; the first point shows about an hour after installing.
- **Roams, short drops and late roams today:** one line per tracked device.
- **Device signal, 24 h:** each device's signal. A jump marks a roam, and a long low
  stretch shows a device that stayed on a far access point.

The coverage sensors exist from 0.7.0b3 on. If the result has empty card lists, update the
integration, restart Home Assistant, and run the template again.

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
        - entity_id: sensor.*_short_drops_today
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
