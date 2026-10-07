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
import re
import time

import asyncssh

from . import (
    AccessPointAuthError,
    AccessPointDriver,
    AccessPointError,
    AssociatedClient,
    DriverField,
    normalize_mac,
    register,
)

RADIOS = (("0", "2.4GHz"), ("1", "5GHz"))

# Older DAP firmware only offers these; listed after the modern ones so newer units
# still negotiate something stronger.
KEX_ALGS = [
    "curve25519-sha256",
    "ecdh-sha2-nistp256",
    "diffie-hellman-group14-sha256",
    "diffie-hellman-group14-sha1",
    "diffie-hellman-group1-sha1",
]
HOST_KEY_ALGS = ["ssh-ed25519", "rsa-sha2-256", "rsa-sha2-512", "ssh-rsa"]
ENCRYPTION_ALGS = ["aes128-ctr", "aes256-ctr", "aes128-cbc", "aes256-cbc", "3des-cbc"]

CONNECT_TIMEOUT = 10
COMMAND_TIMEOUT = 15

_PROMPT = re.compile(r"[\w.\-]+->\s*$")
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
                        rssi=_int(record.get("rssi")),
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
    FIELDS = (
        DriverField("host"),
        DriverField("port", default=22),
        DriverField("username", default="admin"),
        DriverField("password", secret=True),
    )

    async def async_get_associated_clients(self) -> list[AssociatedClient]:
        """Log in, read both radios' association tables and log out."""
        try:
            async with asyncssh.connect(
                self.config["host"],
                port=int(self.config.get("port") or 22),
                username=self.config["username"],
                password=self.config["password"],
                known_hosts=None,
                kex_algs=KEX_ALGS,
                server_host_key_algs=HOST_KEY_ALGS,
                encryption_algs=ENCRYPTION_ALGS,
                connect_timeout=CONNECT_TIMEOUT,
            ) as conn:
                async with conn.create_process(
                    term_type="vt100", term_size=(200, 1000)
                ) as process:
                    await _read_until_prompt(process)
                    clients: list[AssociatedClient] = []
                    for radio, band in RADIOS:
                        await _run(process, f"config wlan {radio}")
                        clients += parse_clientinfo(
                            await _run(process, "get clientinfo"), band
                        )
                    return clients
        except asyncssh.PermissionDenied as err:
            raise AccessPointAuthError(f"{self.config['host']}: login rejected") from err
        except (OSError, asyncio.TimeoutError, asyncssh.Error) as err:
            raise AccessPointError(f"{self.config['host']}: {err!r}") from err


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
