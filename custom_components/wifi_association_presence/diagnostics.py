"""Download diagnostics: the integration's setup, and the last access point report.

The report comes from the "Collect access point report" button. It is kept in memory
only (until Home Assistant restarts or the next collection), already redacted by the
collector. Passwords, SNMP communities and keys are never stored. A failed attempt is
recorded too, so someone who closed the window can see what happened.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

import wifi_ap_associations
from wifi_ap_associations.collect import Redactor

from .const import CONF_DRIVER, DATA_AP_REPORT, SUBENTRY_ACCESS_POINT, SUBENTRY_TRACKED_DEVICE

NO_REPORT = (
    "No report collected since Home Assistant started. To collect one: Settings -> "
    "Devices & services -> Wi-Fi Association Presence -> Collect access point report."
)
METHOD_NAMES = {"ssh": "ssh", "snmp_v2c": "snmp-v2c", "snmp_v3": "snmp-v3"}


def store_report(
    hass: HomeAssistant, *, host: str, method: str, settings: dict[str, Any], report: str
) -> None:
    """Keep the collected report, and how it was collected, for diagnostics."""
    redactor = Redactor([])
    shown = {
        key: [redactor.text(item) for item in value] if key == "extra_commands" else value
        for key, value in settings.items()
    }
    attempt = _attempt(host, method, "ok")
    hass.data[DATA_AP_REPORT] = {
        "last_attempt": attempt,
        "collected_at": attempt["at"],
        "host": attempt["host"],
        "method": attempt["method"],
        **shown,
        "report": report.splitlines(),
    }


def store_failed_attempt(hass: HomeAssistant, *, host: str, method: str, error: str) -> None:
    """Record a failed collection; an earlier successful report is kept."""
    stored = hass.data.setdefault(DATA_AP_REPORT, {})
    stored["last_attempt"] = _attempt(host, method, f"failed: {error}")


def _attempt(host: str, method: str, result: str) -> dict[str, str]:
    return {
        "at": dt_util.utcnow().isoformat(timespec="seconds"),
        "host": _redact_host(host),
        "method": METHOD_NAMES.get(method, method),
        "result": result,
    }


def _redact_host(host: str) -> str:
    """An IP address as the report shows it (e.g. 192.x.x.10); a host name hidden."""
    redacted = Redactor([]).text(host)
    return redacted if redacted != host else "<host name>"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Setup summary (no addresses or credentials) plus the last collected report."""
    return {
        "library_version": wifi_ap_associations.__version__,
        "options": dict(entry.options),
        "access_point_types": sorted(
            str(subentry.data.get(CONF_DRIVER))
            for subentry in entry.get_subentries_of_type(SUBENTRY_ACCESS_POINT)
        ),
        "tracked_devices": len(entry.get_subentries_of_type(SUBENTRY_TRACKED_DEVICE)),
        "access_point_report": hass.data.get(DATA_AP_REPORT) or NO_REPORT,
    }
