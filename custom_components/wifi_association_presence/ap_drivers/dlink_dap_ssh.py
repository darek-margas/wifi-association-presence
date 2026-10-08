"""D-Link DAP access points (DAP-2610, DAP-3662, ...) through the SSH console.

The DAP CLI lists associated clients per radio: "config wlan 0" selects 2.4 GHz and
"config wlan 1" selects 5 GHz, then "get clientinfo" prints one block per client:

    Client1--time:1430
    Client1--ssid: primary SSID
    Client1--mac:76:F4:C1:CB:89:12
    Client1--auth:WPA2-EAP
    Client1--rssi:98
    Client1--mode:11ac
    Client1--psmode:0
    Client1--rx_bytes:1124006
    Client1--tx_bytes:1757062
    ---------------------------------------------------------------------

ClientN numbering restarts for every SSID, so records are keyed by MAC. Note that
"set band 2.4G/5G" does not change what "get clientinfo" shows; "config wlan" does.
These units only offer password logins and old SSH algorithms.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
import re
import time
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

RADIOS = (("0", "2.4GHz"), ("1", "5GHz"))

# DAP-2610 offers only diffie-hellman-group-exchange-sha1, -group14-sha1 and
# -group1-sha1 key exchange, ssh-rsa host keys, and accepts password login (it lists
# publickey and keyboard-interactive, but neither works). Modern algorithms come first
# so newer firmware negotiates something stronger.
DAP_SSH_POLICY = SshPolicy(
    kex_algs=(
        "curve25519-sha256",
        "ecdh-sha2-nistp256",
        "diffie-hellman-group14-sha256",
        # Proven with OpenSSH: +diffie-hellman-group1-sha1,diffie-hellman-group14-sha1
        "diffie-hellman-group14-sha1",
        "diffie-hellman-group1-sha1",
        "diffie-hellman-group-exchange-sha1",
    ),
    server_host_key_algs=("ssh-ed25519", "rsa-sha2-256", "rsa-sha2-512", "ssh-rsa"),
    encryption_algs=("aes128-ctr", "aes256-ctr", "aes128-cbc", "aes256-cbc", "3des-cbc"),
    preferred_auth=("password",),
)

COMMAND_TIMEOUT = 15
CACHE_TTL = 3600  # re-read SSID names and static device details hourly

_PROMPT = re.compile(r"[\w.\-]+->\s*$")
_SSID_LABEL = re.compile(r"^(?:primary ssid|multi-ssid index (\d+))$", re.IGNORECASE)
_IS_VALUE = re.compile(r"^.*\(index \d+\) is (.*)$", re.IGNORECASE)
_UPTIME = re.compile(r"Day\s+(\d+),\s*(\d+):(\d+):(\d+)", re.IGNORECASE)
MAX_VALUE_LENGTH = 64  # SSIDs are at most 32 characters; names/locations similar
_ERROR_REPLY = re.compile(
    r"\b(invalid|error|unknown command|wrong input|unable to|can'?t|cannot|not found|"
    r"failed|not supported|no such|usage:)",
    re.IGNORECASE,
)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_FIELD = re.compile(r"^Client\d+--(\w+):\s*(.*?)\s*$")


def parse_clientinfo(text: str, band: str | None = None) -> list[AssociatedClient]:
    """Parse "get clientinfo" output into clients (blocks without a MAC are dropped)."""
    clients: list[AssociatedClient] = []
    record: dict[str, str] = {}

    def flush() -> None:
        if mac := record.get("mac"):
            try:
                normalized = normalize_mac(mac)
            except ValueError:
                pass
            else:
                clients.append(
                    AssociatedClient(
                        mac=normalized,
                        ssid=record.get("ssid") or None,
                        band=band,
                        signal=_int(record.get("rssi")),  # percent on DAP
                        connected_seconds=_int(record.get("time")),
                    )
                )
        record.clear()

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("----"):
            flush()
            continue
        if match := _FIELD.match(line):
            key, value = match.groups()
            if key in record:  # a new record started without a separator
                flush()
            record[key] = value
    flush()
    return clients


def parse_uptime(output: str) -> int | None:
    """Seconds from "AP Uptime -- Day 62,  0:41:46"."""
    if match := _UPTIME.search(output):
        days, hours, minutes, seconds = (int(part) for part in match.groups())
        return ((days * 24 + hours) * 60 + minutes) * 60 + seconds
    return None


def parse_hardware(output: str) -> str | None:
    """Revision from "rev A1G"."""
    value = parse_cli_value(output, "get hardware")
    if value and value.lower().startswith("rev "):
        value = value[4:].strip()
    return value or None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


@register
class DlinkDapSsh(AccessPointDriver):
    """D-Link DAP series over the SSH console."""

    TYPE = "dlink_dap_ssh"
    NAME = "D-Link DAP (SSH console)"
    MANUFACTURER = "D-Link"
    SIGNAL_UNIT = "%"
    # No model over the CLI (it is entered in the access point's settings instead).
    REPORTS = frozenset(
        {"name", "location", "firmware", "hardware", "uptime_seconds",
         "cpu_percent", "memory_percent"}
    )
    SSH_POLICY = DAP_SSH_POLICY
    FIELDS = (
        DriverField("host"),
        DriverField("port", default=22),
        DriverField("username", default="admin"),
        DriverField("password", secret=True),
    )

    def __init__(self, config: dict[str, Any]) -> None:
        """Set up the caches for SSID names and static device details."""
        super().__init__(config)
        self._ssid_names: dict[tuple[str, str], str] = {}
        self._static_info: AccessPointInfo | None = None
        self._cache_read_at = 0.0

    async def async_get_associated_clients(self) -> list[AssociatedClient]:
        """Log in, read both radios' association tables and log out."""
        self._expire_cache()
        async with self._console() as process:
            return await self._read_clients(process)

    async def async_poll(self) -> PollResult:
        """Clients plus device details, in one console session."""
        self._expire_cache()
        async with self._console() as process:
            clients = await self._read_clients(process)
            if self._static_info is None:
                self._static_info = AccessPointInfo(
                    name=parse_cli_value(await _run(process, "get systemname"), "get systemname"),
                    location=parse_cli_value(
                        await _run(process, "get location"), "get location"
                    ),
                    firmware=parse_cli_value(await _run(process, "version"), "version"),
                    hardware=parse_hardware(await _run(process, "get hardware")),
                )
            info = replace(
                self._static_info,
                uptime_seconds=parse_uptime(await _run(process, "get uptime")),
                cpu_percent=_int(parse_cli_value(await _run(process, "get cpuinfo"), "get cpuinfo")),
                memory_percent=_int(
                    parse_cli_value(await _run(process, "get meminfo"), "get meminfo")
                ),
            )
            return PollResult(clients, info)

    def _expire_cache(self) -> None:
        """Re-read SSID names and static device details hourly."""
        if time.monotonic() - self._cache_read_at > CACHE_TTL:
            self._ssid_names.clear()
            self._static_info = None
            self._cache_read_at = time.monotonic()

    async def _read_clients(self, process: asyncssh.SSHClientProcess) -> list[AssociatedClient]:
        """Both radios' association tables, with SSID labels resolved to names."""
        clients: list[AssociatedClient] = []
        for radio, band in RADIOS:
            await _run(process, f"config wlan {radio}")
            radio_clients = parse_clientinfo(await _run(process, "get clientinfo"), band)
            for label in {c.ssid for c in radio_clients if c.ssid}:
                if (band, label) in self._ssid_names:
                    continue
                command = ssid_name_command(label)
                name = (
                    parse_cli_value(await _run(process, command), command) if command else None
                )
                # Unresolvable labels are cached as-is until the next refresh.
                self._ssid_names[(band, label)] = name or label
            clients += [
                replace(c, ssid=self._ssid_names.get((band, c.ssid or ""), c.ssid))
                for c in radio_clients
            ]
        return clients

    async def async_run_commands(self, commands: list[str]) -> list[str]:
        """Run read-only console commands and return their raw output (for diagnostics)."""
        async with self._console() as process:
            return [await _run(process, command) for command in commands]

    @asynccontextmanager
    async def _console(self) -> AsyncIterator[asyncssh.SSHClientProcess]:
        """Logged-in console session, with errors mapped to AccessPointError."""
        try:
            async with asyncssh.connect(
                self.config["host"],
                port=int(self.config.get("port") or 22),
                username=self.config["username"],
                password=self.config["password"],
                **self.SSH_POLICY.connect_options(),
            ) as conn:
                async with conn.create_process(
                    term_type="vt100",
                    term_size=(200, 1000),
                    encoding="utf-8",
                    errors="replace",  # never fail on odd bytes from the console
                ) as process:
                    await _read_until_prompt(process)
                    yield process
        except asyncssh.PermissionDenied as err:
            raise AccessPointAuthError(f"{self.config['host']}: login rejected") from err
        except (OSError, asyncio.TimeoutError, asyncssh.Error) as err:
            raise AccessPointError(f"{self.config['host']}: {err!r}") from err


def ssid_name_command(label: str) -> str | None:
    """CLI command that prints the name for a clientinfo SSID label."""
    if match := _SSID_LABEL.match(label.strip()):
        return f"get multi-ssid {match.group(1)}" if match.group(1) else "get ssid"
    return None


def parse_cli_value(output: str, command: str) -> str | None:
    """Value printed by a "get" command.

    Seen on DAP-2610/DAP-3662: "get ssid" -> "SSID:Power" and
    "get multi-ssid 3" -> "SSID of Multi-SSID (index 3) is Internal".
    """
    lines = [
        line.strip()
        for line in output.splitlines()
        if line.strip() and command not in line and not _PROMPT.search(line.strip())
    ]
    if not lines:
        return None
    last = lines[-1]
    # Judge the whole line: in "Invalid parameter: 9" the error is before the colon.
    if _ERROR_REPLY.search(last):
        return None
    if match := _IS_VALUE.match(last):
        value = match.group(1).strip()
    elif ":" in last:
        value = last.split(":", 1)[1].strip()
    else:
        value = last
    return _clean_value(value)


def _clean_value(value: str) -> str | None:
    """Reject replies that are error messages or nonsense rather than a value.

    Other firmware versions answer unsupported commands with messages such as
    "Wrong input parameters ..." or "Unable to open device ..."; those must not end
    up as an SSID name or location.
    """
    value = "".join(ch for ch in value if ch.isprintable()).strip()
    if not value or len(value) > MAX_VALUE_LENGTH or _ERROR_REPLY.search(value):
        return None
    return value


async def _run(process: asyncssh.SSHClientProcess, command: str) -> str:
    """Send a CLI command and return its output up to the next prompt."""
    process.stdin.write(command + "\n")
    return await _read_until_prompt(process)


async def _read_until_prompt(process: asyncssh.SSHClientProcess) -> str:
    """Read console output until the "XXX->" prompt appears."""
    deadline = time.monotonic() + COMMAND_TIMEOUT
    buffer = ""
    while not _PROMPT.search(buffer):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AccessPointError(f"no CLI prompt within {COMMAND_TIMEOUT}s")
        chunk = await asyncio.wait_for(process.stdout.read(4096), remaining)
        if not chunk:
            raise AccessPointError("console closed the connection")
        buffer = _ANSI.sub("", buffer + chunk).replace("\r", "")
    return buffer
