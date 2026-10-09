"""Constants for Wi-Fi Association Presence."""

from __future__ import annotations

from datetime import timedelta
import logging

DOMAIN = "wifi_association_presence"
LOGGER = logging.getLogger(__package__)

SUBENTRY_ACCESS_POINT = "access_point"
SUBENTRY_TRACKED_DEVICE = "tracked_device"
# A button on the integration page that collects a report; never stored as a subentry.
SUBENTRY_REPORT = "access_point_report"

CONF_DRIVER = "driver"
CONF_MAC = "mac"
CONF_NAME = "name"
CONF_MODEL = "model"
CONF_CONSIDER_HOME = "consider_home"

# Collect access point report (for adding support for an access point).
CONF_PROFILE = "profile"
CONF_COMMANDS = "commands"
CONF_LEGACY_SSH = "legacy_ssh"
CONF_COMMUNITY = "community"
CONF_AUTH_KEY = "auth_key"
CONF_PRIV_KEY = "priv_key"
CONF_AUTH_PROTOCOL = "auth_protocol"
CONF_PRIV_PROTOCOL = "priv_protocol"
# Never stored, never shown again in a form.
SECRET_KEYS = frozenset({"password", CONF_COMMUNITY, CONF_AUTH_KEY, CONF_PRIV_KEY})
# How a report can be collected (menu order).
COLLECT_METHODS = ("ssh", "snmp_v2c", "snmp_v3")
# Login plus up to ~10 commands, each waited on for at most 20 s; most finish in seconds.
COLLECT_TIMEOUT = 180
# An SNMP walk of a vendor subtree can be long; each subtree stops after this many values.
SNMP_COLLECT_TIMEOUT = 300
SNMP_MAX_VALUES = 5000
# hass.data key of the last collected report, kept in memory for Download diagnostics.
DATA_AP_REPORT = f"{DOMAIN}_ap_report"

# Fixed by design (Home Assistant integrations don't expose polling intervals).
SCAN_INTERVAL = timedelta(seconds=60)
# Grace period (seconds, user-adjustable in the options): a device stays "home" this
# long after it was last seen associated, so roaming between APs, a coverage gap, a
# missed poll or a failing AP doesn't flip it to "away".
DEFAULT_CONSIDER_HOME = 180
