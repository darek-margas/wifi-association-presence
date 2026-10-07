"""Constants for Wi-Fi Association Presence."""

from __future__ import annotations

from datetime import timedelta
import logging

DOMAIN = "wifi_association_presence"
LOGGER = logging.getLogger(__package__)

SUBENTRY_ACCESS_POINT = "access_point"
SUBENTRY_TRACKED_DEVICE = "tracked_device"

CONF_DRIVER = "driver"
CONF_MAC = "mac"

# Fixed by design (Home Assistant integrations don't expose polling intervals).
SCAN_INTERVAL = timedelta(seconds=60)
# A device stays "home" this long after it was last seen associated, so a missed
# poll, a failed AP or roaming between APs doesn't flip it to "away".
CONSIDER_HOME = timedelta(minutes=3)
