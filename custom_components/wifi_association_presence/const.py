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
CONF_NAME = "name"
CONF_CONSIDER_HOME = "consider_home"

# Fixed by design (Home Assistant integrations don't expose polling intervals).
SCAN_INTERVAL = timedelta(seconds=60)
# Grace period (seconds, user-adjustable in the options): a device stays "home" this
# long after it was last seen associated, so roaming between APs, a coverage gap, a
# missed poll or a failing AP doesn't flip it to "away".
DEFAULT_CONSIDER_HOME = 180
