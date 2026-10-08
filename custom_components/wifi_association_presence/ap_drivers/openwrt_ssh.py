"""OpenWrt access points over SSH, reading hostapd through ubus.

Every hostapd radio/SSID on OpenWrt registers a ubus object (hostapd.wlan0 on older
releases, hostapd.phy0-ap0 from 23.05). Its JSON answers, from OpenWrt's hostapd
ubus.c, are:

    ubus call hostapd.phy0-ap0 get_status
    {"status": "ENABLED", "ssid": "Home", "freq": 5180, "channel": 36, "phy": "phy0", ...}

    ubus call hostapd.phy0-ap0 get_clients
    {"freq": 5180, "clients": {"5c:ad:ba:00:00:01": {"auth": true, "assoc": true,
     "authorized": true, "aid": 1, "signal": -52, "bytes": {...}, ...}}}

A station is listed while it authenticates too, so only associated and authorized ones
count (as Home Assistant's own ubus tracker does). "system board" and "system info"
give the name, model, firmware, uptime and memory.

One SSH command per poll runs all of it and marks each section, so the console
dialogue of the D-Link driver isn't needed: OpenWrt's dropbear runs commands directly.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import asyncssh

from . import (
    AccessPointAuthError,
    AccessPointDriver,
    AccessPointError,
    AccessPointInfo,
    AssociatedClient,
    DriverField,
    PollResult,
    normalize_mac,
    register,
)
from .ssh import SshPolicy

# Current dropbear and OpenSSH algorithms, plus the ones of older OpenWrt releases.
OPENWRT_SSH_POLICY = SshPolicy(
    kex_algs=(
        "curve25519-sha256",
        "curve25519-sha256@libssh.org",
        "ecdh-sha2-nistp256",
        "diffie-hellman-group14-sha256",
        "diffie-hellman-group14-sha1",
    ),
    server_host_key_algs=(
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "rsa-sha2-256",
        "rsa-sha2-512",
        "ssh-rsa",
    ),
    encryption_algs=(
        "chacha20-poly1305@openssh.com",
        "aes128-ctr",
        "aes256-ctr",
        "aes128-gcm@openssh.com",
        "aes256-gcm@openssh.com",
    ),
    preferred_auth=("password", "keyboard-interactive"),
)

COMMAND_TIMEOUT = 20
MARK = "@@wap@@"
# One command, every section marked: board, info, then status and clients per radio.
POLL_COMMAND = (
    f"echo '{MARK} board'; ubus call system board; "
    f"echo '{MARK} info'; ubus call system info; "
    "for o in $(ubus list 'hostapd.*'); do "
    f'echo "{MARK} status $o"; ubus call "$o" get_status; '
    f'echo "{MARK} clients $o"; ubus call "$o" get_clients; '
    "done"
)
_MARK_LINE = re.compile(rf"^{re.escape(MARK)} (\w+)(?: (\S+))?\s*$")


def band_for_frequency(freq: Any) -> str | None:
    """2.4GHz / 5GHz / 6GHz from a channel frequency in MHz."""
    if not isinstance(freq, int) or isinstance(freq, bool):
        return None
    if 2400 <= freq < 2500:
        return "2.4GHz"
    if 4900 <= freq < 5925:
        return "5GHz"
    if 5925 <= freq < 7125:
        return "6GHz"
    return None


def _signed_dbm(value: Any) -> int | None:
    """Signal in dBm; hostapd adds it as a u32, so -52 may arrive as 4294967244."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value >= 2**31:
        value -= 2**32
    return value if -120 <= value <= 0 else None


def split_sections(output: str) -> list[tuple[str, str | None, str]]:
    """(kind, ubus object, body) for each marked section of the poll output."""
    sections: list[tuple[str, str | None, str]] = []
    kind: str | None = None
    obj: str | None = None
    body: list[str] = []
    for line in output.splitlines():
        if match := _MARK_LINE.match(line):
            if kind is not None:
                sections.append((kind, obj, "\n".join(body)))
            kind, obj, body = match.group(1), match.group(2), []
        elif kind is not None:
            body.append(line)
    if kind is not None:
        sections.append((kind, obj, "\n".join(body)))
    return sections


def _json(body: str) -> dict[str, Any] | None:
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_clients(get_clients: dict[str, Any], ssid: str | None) -> list[AssociatedClient]:
    """Associated and authorized stations of one hostapd interface."""
    band = band_for_frequency(get_clients.get("freq"))
    stations = get_clients.get("clients")
    if not isinstance(stations, dict):
        return []
    clients: list[AssociatedClient] = []
    for mac, station in stations.items():
        if not isinstance(station, dict):
            continue
        if station.get("assoc") is False or station.get("authorized") is False:
            continue
        try:
            normalized = normalize_mac(mac)
        except ValueError:
            continue
        clients.append(
            AssociatedClient(
                mac=normalized,
                ssid=ssid,
                band=band,
                signal=_signed_dbm(station.get("signal")),
            )
        )
    return clients


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if 0 < len(value) <= 64 and value.isprintable() else None


def parse_info(board: dict[str, Any] | None, info: dict[str, Any] | None) -> AccessPointInfo:
    """Name, model, firmware, uptime and memory from "system board" and "system info"."""
    board = board or {}
    info = info or {}
    release = board.get("release") if isinstance(board.get("release"), dict) else {}
    memory = info.get("memory") if isinstance(info.get("memory"), dict) else {}
    memory_percent = None
    total = memory.get("total")
    available = memory.get("available", memory.get("free"))
    if isinstance(total, int) and total > 0 and isinstance(available, int):
        memory_percent = max(0, min(100, round(100 * (total - available) / total)))
    uptime = info.get("uptime")
    return AccessPointInfo(
        name=_text(board.get("hostname")),
        model=_text(board.get("model")),
        firmware=_text(release.get("description")) or _text(release.get("version")),
        hardware=_text(board.get("board_name")),
        uptime_seconds=uptime if isinstance(uptime, int) and uptime >= 0 else None,
        memory_percent=memory_percent,
    )


def parse_poll(output: str) -> PollResult:
    """Clients of every radio, and what the AP reports about itself.

    Unparsable sections are skipped (a radio being reconfigured, a truncated reply);
    clients of a radio whose status can't be read are kept without an SSID.
    """
    board = info = None
    ssids: dict[str, str | None] = {}
    clients: list[AssociatedClient] = []
    for kind, obj, body in split_sections(output):
        data = _json(body)
        if data is None:
            continue
        if kind == "board":
            board = data
        elif kind == "info":
            info = data
        elif kind == "status" and obj:
            ssids[obj] = _text(data.get("ssid"))
        elif kind == "clients" and obj:
            clients.extend(parse_clients(data, ssids.get(obj)))
    return PollResult(clients, parse_info(board, info))


@register
class OpenWrtSsh(AccessPointDriver):
    """OpenWrt (or any hostapd + ubus system) over SSH."""

    TYPE = "openwrt_ssh"
    NAME = "OpenWrt (SSH, ubus)"
    MANUFACTURER = "OpenWrt"
    SIGNAL_UNIT = "dBm"
    # OpenWrt has no location setting, and reports load averages rather than CPU %.
    REPORTS = frozenset(
        {"name", "model", "firmware", "hardware", "uptime_seconds", "memory_percent"}
    )
    SSH_POLICY = OPENWRT_SSH_POLICY
    FIELDS = (
        DriverField("host"),
        DriverField("port", default=22),
        DriverField("username", default="root"),
        DriverField("password", secret=True),
    )

    async def async_get_associated_clients(self) -> list[AssociatedClient]:
        """Clients associated to any radio of the AP."""
        return (await self.async_poll()).clients

    async def async_poll(self) -> PollResult:
        """Clients and device details, from one SSH command."""
        output = await self._run(POLL_COMMAND)
        if "ubus" in output and "not found" in output and MARK not in output:
            raise AccessPointError(f"{self.config['host']}: ubus is not available")
        return parse_poll(output)

    async def _run(self, command: str) -> str:
        try:
            async with asyncssh.connect(
                self.config["host"],
                port=int(self.config.get("port") or 22),
                username=self.config["username"],
                password=self.config["password"],
                **self.SSH_POLICY.connect_options(),
            ) as conn:
                result = await asyncio.wait_for(
                    conn.run(command, check=False, encoding="utf-8", errors="replace"),
                    COMMAND_TIMEOUT,
                )
        except asyncssh.PermissionDenied as err:
            raise AccessPointAuthError(f"{self.config['host']}: login rejected") from err
        except (OSError, asyncio.TimeoutError, asyncssh.Error) as err:
            raise AccessPointError(f"{self.config['host']}: {err!r}") from err
        return f"{result.stdout or ''}\n{result.stderr or ''}"
