#!/usr/bin/env python3
"""Collect read-only information from an access point to help add support for it.

Two modes:

SSH console (default): logs in, runs a list of read-only commands.
    python3 scripts/collect.py --host 192.0.2.10 --username admin
    python3 scripts/collect.py --host 192.0.2.10 --username admin --profile dlink_dap
    python3 scripts/collect.py --host 192.0.2.10 --username admin --command "show wireless clients"

SNMP (--snmp): reads sysObjectID to find the vendor's private MIB subtree (its
enterprise number), walks it plus the standard 802.11 and bridge tables, and lists the
tables that look like client tables (MAC addresses in values or indexes).
    python3 scripts/collect.py --host 192.0.2.10 --snmp                 # v2c, asks for community
    python3 scripts/collect.py --host 192.0.2.10 --snmp --snmp-user ro  # v3, SHA/AES
    python3 scripts/collect.py --host 192.0.2.10 --snmp --snmp-root 1.3.6.1.4.1.14988.1.1.1.2

Both redact MAC addresses (vendor prefix kept, also inside SNMP OID indexes), IPv4
addresses and secret-looking settings, and write a report you can attach to a GitHub
issue ("New access point model"). Review the report before sharing it.

Options:
    --profile NAME     SSH: a known command list (see --list-profiles); default: generic
    --command CMD      SSH: extra command to run (repeatable)
    --legacy-ssh       SSH: allow old algorithms (diffie-hellman-group1/14-sha1, ssh-rsa, CBC)
    --snmp             use SNMP instead of SSH
    --snmp-user USER   SNMP: use v3 with this user instead of v2c
    --snmp-auth ALG    SNMPv3 authentication: md5, sha (SHA-1, default), sha224, sha256,
                       sha384, sha512
    --snmp-priv ALG    SNMPv3 privacy: des, 3des, aes (AES-128, default), aes192, aes256,
                       aes192c / aes256c (Cisco's key extension)
    --snmp-root OID    SNMP: extra subtree to walk (repeatable)
    --snmp-max N       SNMP: stop each subtree after N values (default 20000)
    --snmp-full-values SNMP: show all text values, not only in the client tables
                       (vendor MIBs can hold keys or passphrases: review first)
    --no-redact        keep MAC/IP addresses (only for your own debugging; don't share)
    --output FILE      report file (default: ap-report-<host>.txt)

Requires: pip install asyncssh   (SSH mode)
          pip install pysnmp     (SNMP mode)
Secrets are asked for interactively, or taken from $AP_PASSWORD (SSH password, SNMPv3
auth key), $AP_PRIV_KEY (SNMPv3 privacy key, defaults to the auth key) and
$AP_COMMUNITY (SNMP v2c community).
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import getpass
import json
import os
import re
import sys
import time
import warnings

warnings.filterwarnings("ignore", message="Diffie-Hellman over finite fields")

# Commands only display information. Anything that changes settings (set/apply/save,
# reboot, factory reset, ...) is refused, whatever the profile or --command says.
PROFILES: dict[str, list[str]] = {
    "generic": [
        "help",
        "?",
        "show version",
        "show system",
        "show wireless",
        "show wireless clients",
        "show clients",
        "show station",
        "get",
        "version",
    ],
    # UniFi APs: SSH with the device credentials set in the UniFi controller.
    # mca-dump prints the AP's state as JSON, including each radio's station table.
    "unifi": [
        "info",
        "mca-dump",
    ],
    # Cisco wireless LAN controllers (Catalyst 9800, IOS-XE). Lightweight APs (9120,
    # 3802, 1815...) don't list clients themselves; the controller does.
    # OpenWrt: hostapd on ubus (JSON), the same calls the OpenWrt driver uses.
    "openwrt": [
        "ubus call system board",
        "ubus call system info",
        "ubus list 'hostapd.*'",
        "for o in $(ubus list 'hostapd.*'); do echo \"== $o\"; ubus call \"$o\" get_status;"
        " ubus call \"$o\" get_clients; done",
        "iwinfo",
    ],
    "cisco_wlc": [
        "terminal length 0",
        "show version | include Cisco IOS|uptime|Model",
        "show ap summary",
        "show wlan summary",
        "show wireless client summary",
        "show wireless client summary detail",
    ],
    "dlink_dap": [
        "help",
        "get",
        "version",
        "get hardware",
        "get systemname",
        "get location",
        "get uptime",
        "get cpuinfo",
        "get meminfo",
        "config wlan 0",
        "get ssid",
        "get clientinfo",
        "config wlan 1",
        "get ssid",
        "get clientinfo",
    ],
}

_REFUSED = re.compile(
    r"^\s*(set|apply|save|write|commit|delete|del|clear|reset|reboot|restart|reload|"
    r"factory|upgrade|fota|erase|format|copy|passwd|password|snmp\s+(add|del|edit|resume|suspend)|"
    r"ssl\s+freset|ping|traceroute)\b",
    re.IGNORECASE,
)
# Lines that may carry secrets: keys, passphrases, RADIUS secrets, community strings.
_SECRET_LINE = re.compile(
    r"(pass(word|phrase)?|secret|psk|key|community|token|credential|serial)", re.IGNORECASE
)
_HELP_LINE = re.compile(r"^\s*\S.*?\s{2,}--\s")
_MAC = re.compile(r"\b([0-9A-Fa-f]{2})([:-])([0-9A-Fa-f]{2})\2([0-9A-Fa-f]{2})(?:\2[0-9A-Fa-f]{2}){3}\b")
# Cisco style (aabb.ccdd.eeff) and bare (aabbccddeeff) MACs.
_MAC_DOTTED = re.compile(r"\b[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\b")
_MAC_BARE = re.compile(r"\b[0-9A-Fa-f]{12}\b")
_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# 16+ hex digits in one run: keys, password hashes, serials. (MACs have separators.)
_LONG_HEX = re.compile(r"\b[0-9A-Fa-f]{16,}\b")
# IPv6, full (8 groups) or compressed ("::"), optionally with a zone (%eth0). Link-local
# addresses can embed the client's MAC (EUI-64). Times like 14:01:02 don't match.
_IPV6 = re.compile(
    r"(?<![0-9A-Fa-f:])(?:"
    r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?"
    r")(?:%[\w.]+)?(?![0-9A-Fa-f:])"
)
# Cisco serial numbers (e.g. FGL2412AB12: 3 letters, 4 digits, 4 letters/digits).
_SERIAL_CISCO = re.compile(r"\b[A-Z]{3}\d{4}[A-Z0-9]{4}\b")
_PROMPT = re.compile(r"(\S{0,40}[>#$%:]|->)\s*$")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

LEGACY = {
    "kex_algs": [
        "curve25519-sha256",
        "ecdh-sha2-nistp256",
        "diffie-hellman-group14-sha256",
        "diffie-hellman-group14-sha1",
        "diffie-hellman-group1-sha1",
        "diffie-hellman-group-exchange-sha1",
    ],
    "server_host_key_algs": ["ssh-ed25519", "rsa-sha2-256", "rsa-sha2-512", "ssh-rsa"],
    "encryption_algs": ["aes128-ctr", "aes256-ctr", "aes128-cbc", "aes256-cbc", "3des-cbc"],
}

# --- redaction ----------------------------------------------------------------------


class Redactor:
    """Consistent redaction: the same MAC always becomes the same placeholder."""

    def __init__(self, secrets: list[str]) -> None:
        self._macs: dict[str, str] = {}
        self._secrets = [s for s in secrets if s]

    def mac(self, octets: list[int]) -> str:
        """Placeholder for a MAC: vendor prefix kept, e.g. 5C:AD:BA:XX:XX:01."""
        full = ":".join(f"{o:02X}" for o in octets)
        if full not in self._macs:
            self._macs[full] = ":".join(full.split(":")[:3]) + f":XX:XX:{len(self._macs) + 1:02d}"
        return self._macs[full]

    def _mac_text(self, match: re.Match[str]) -> str:
        sep = match.group(2)
        octets = [int(part, 16) for part in re.split(r"[:-]", match.group(0))]
        return self.mac(octets).replace(":", sep)

    def _mac_compact(self, match: re.Match[str], dotted: bool) -> str:
        """aabb.ccdd.eeff / aabbccddeeff -> aabb.ccxx.xx01 / aabbccxxxx01 (prefix kept)."""
        digits = match.group(0).replace(".", "")
        octets = [int(digits[i : i + 2], 16) for i in range(0, 12, 2)]
        placeholder = self.mac(octets).replace(":", "").lower()
        if dotted:
            return f"{placeholder[0:4]}.{placeholder[4:8]}.{placeholder[8:12]}"
        return placeholder

    def text(self, line: str) -> str:
        """Hide MACs, IPv4 and email addresses, key-like hex and this run's secrets."""
        for secret in self._secrets:
            line = line.replace(secret, "<redacted>")
        line = _EMAIL.sub("<email>", line)
        line = _LONG_HEX.sub("<hex>", line)
        line = _MAC.sub(self._mac_text, line)
        line = _MAC_DOTTED.sub(lambda m: self._mac_compact(m, dotted=True), line)
        line = _MAC_BARE.sub(lambda m: self._mac_compact(m, dotted=False), line)
        line = _IPV6.sub(_ipv6_placeholder, line)
        line = _SERIAL_CISCO.sub("<serial>", line)
        return _IPV4.sub(lambda m: f"{m.group(1)}.x.x.{m.group(4)}", line)


def _ipv6_placeholder(match: re.Match[str]) -> str:
    """fe80::1c2b:3cff:fe4d:5e6f -> fe80::<v6> (first group kept; a bare "::" stays)."""
    text = match.group(0)
    if text == "::":
        return text
    first = text.split(":", 1)[0]
    return f"{first}::<v6>" if first else "::<v6>"


def _split_setting(line: str) -> tuple[str, str, str]:
    """Split "name: value" / "name=value" at the first separator ("", "", line if none)."""
    match = re.search(r"[:=]", line)
    if not match:
        return line, "", ""
    return line[: match.start()], match.group(0), line[match.end() :]


def redact(text: str, redactor: Redactor | None = None) -> str:
    """Redact console output: MACs, IPv4 addresses and secret-looking settings."""
    redactor = redactor or Redactor([])
    lines = []
    for line in text.splitlines():
        # Judge by the setting's name (before ":"/"="), so "auth:WPA2-PSK" stays readable.
        # Help listings ("get key  -- Display Encryption Key (index:1--4)") describe
        # commands rather than show values, so they are left intact.
        # Table rows ("aabb.ccdd.eeff  AP01  1  Run  WPA2 PSK  14:01:02 ...") are not
        # settings: their "name" part has column gaps, and judging it would blank the row.
        name, sep, _value = _split_setting(line)
        is_setting = len(name.strip()) <= 48 and "  " not in name.strip() and "\t" not in name
        if sep and is_setting and not _HELP_LINE.match(line) and _SECRET_LINE.search(name):
            line = f"{name}{sep} <redacted>"
        lines.append(redactor.text(line))
    return "\n".join(lines)


# --- SSH console --------------------------------------------------------------------


async def read_until_prompt(process, timeout: float) -> str:
    """Read until output stops at something that looks like a prompt (or goes quiet)."""
    buffer = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = await asyncio.wait_for(process.stdout.read(4096), 1.5)
        except asyncio.TimeoutError:
            if buffer and _PROMPT.search(buffer.rstrip("\n")):
                break
            continue
        if not chunk:
            break
        buffer = _ANSI.sub("", buffer + chunk).replace("\r", "")
    return buffer


def _pretty_json(output: str) -> str:
    """Re-indent a JSON reply (e.g. UniFi mca-dump) one key per line.

    The redaction works line by line and judges a setting by its name, so a JSON
    document on a single line would hide secret keys from it.
    """
    start, end = output.find("{"), output.rfind("}")
    if start < 0 or end <= start:
        return output
    try:
        document = json.loads(output[start : end + 1])
    except ValueError:
        return output
    return output[:start] + json.dumps(document, indent=1) + output[end + 1 :]


async def collect_ssh(args: argparse.Namespace, password: str) -> str:
    """Log in, run the commands, return the raw transcript."""
    import asyncssh

    options = {
        "known_hosts": None,
        "client_keys": None,
        "agent_path": None,
        "preferred_auth": ["password", "keyboard-interactive"],
        "connect_timeout": 15,
    }
    if args.legacy_ssh:
        options |= LEGACY
    commands = PROFILES[args.profile] + (args.command or [])
    transcript = [f"# profile: {args.profile}  legacy-ssh: {args.legacy_ssh}"]
    async with asyncssh.connect(
        args.host, port=args.port, username=args.username, password=password, **options
    ) as conn:
        transcript.append(f"# server version: {conn.get_extra_info('server_version')}")
        async with conn.create_process(
            term_type="vt100", term_size=(200, 1000), encoding="utf-8", errors="replace"
        ) as process:
            transcript.append("# --- login banner / prompt ---")
            transcript.append(await read_until_prompt(process, 10))
            for command in commands:
                if _REFUSED.match(command):
                    transcript.append(f"# --- {command!r} REFUSED (not read-only) ---")
                    continue
                transcript.append(f"# --- {command} ---")
                process.stdin.write(command + "\n")
                transcript.append(_pretty_json(await read_until_prompt(process, 20)))
    return "\n".join(transcript)


def run_ssh(args: argparse.Namespace) -> str | None:
    """SSH mode; returns the redacted (or raw) report, or None after printing an error."""
    try:
        import asyncssh
    except ImportError:
        print("SSH mode needs asyncssh: pip install asyncssh", file=sys.stderr)
        return None
    password = os.environ.get("AP_PASSWORD") or getpass.getpass(f"{args.host} password: ")
    try:
        transcript = asyncio.run(collect_ssh(args, password))
    except asyncssh.PermissionDenied:
        print("Login rejected. Check username/password.", file=sys.stderr)
        return None
    except (OSError, asyncssh.Error) as err:
        print(f"Connection failed: {err!r}", file=sys.stderr)
        if "no matching" in str(err).lower() or "algorithm" in str(err).lower():
            print("Try again with --legacy-ssh.", file=sys.stderr)
        return None
    return transcript if args.no_redact else redact(transcript, Redactor([password]))


# --- SNMP ---------------------------------------------------------------------------

SYSTEM_OIDS = {
    "sysDescr": "1.3.6.1.2.1.1.1.0",
    "sysObjectID": "1.3.6.1.2.1.1.2.0",
    "sysUpTime": "1.3.6.1.2.1.1.3.0",
    "sysName": "1.3.6.1.2.1.1.5.0",
}
# Standard subtrees worth comparing with the vendor's own tables.
STANDARD_ROOTS = {
    "IEEE 802.11 MIB": "1.2.840.10036",
    "BRIDGE-MIB forwarding table (dot1dTpFdbTable)": "1.3.6.1.2.1.17.4.3",
    "IF-MIB interface names (ifDescr)": "1.3.6.1.2.1.2.2.1.2",
}
ENTERPRISE_PREFIX = "1.3.6.1.4.1."
# Client and AP tables some vendors keep outside their own enterprise subtree, walked
# in addition to it (e.g. Cisco 9800 controllers serve the Airespace wireless MIB).
VENDOR_EXTRA_ROOTS = {
    9: {
        "Airespace client table (bsnMobileStationTable)": "1.3.6.1.4.1.14179.2.1.4",
        "Airespace AP table (bsnAPTable)": "1.3.6.1.4.1.14179.2.2.1",
        "CISCO-LWAPP-DOT11-CLIENT-MIB (cldcClientTable)": "1.3.6.1.4.1.9.9.599.1.3.1",
    },
}
# Only for the report header; the enterprise number itself is what matters.
KNOWN_ENTERPRISES = {
    9: "Cisco",
    171: "D-Link",
    2011: "Huawei",
    4526: "Netgear",
    8072: "net-snmp (Linux, OpenWrt, ...)",
    11863: "TP-Link",
    14179: "Cisco (Airespace WLC)",
    14823: "Aruba",
    14988: "MikroTik",
    25053: "Ruckus",
    41112: "Ubiquiti",
}


def _octets(value) -> bytes | None:
    try:
        return bytes(value.asOctets())
    except AttributeError:
        return None


def _format_value(value) -> str:
    """TYPE: value, with 6-byte strings shown as MACs and binary as hex."""
    kind = value.__class__.__name__
    octets = _octets(value)
    if octets is not None and kind == "OctetString":
        # Any 6 bytes are a MAC unless they are plain printable ASCII (a 6-letter name):
        # MAC bytes are often printable in Latin-1, and a MAC must never leak as hex.
        if len(octets) == 6 and not (octets.isascii() and octets.decode("ascii").isprintable()):
            return f"MAC: {':'.join(f'{b:02X}' for b in octets)}"
        try:
            text = octets.decode("utf-8").rstrip("\r\n\0")
        except UnicodeDecodeError:
            text = None
        if text is not None and text.isprintable():
            return f"STRING: {text!r}"
        return f"HEX: {octets.hex(' ')}"
    return f"{kind}: {value.prettyPrint()}"


# Components before this many are the MIB path ("1.3.6.1.4.1.<vendor>"); a MAC or IP
# can only be in what follows.
PATH_LENGTH = 7
_PRIVATE_IPV4 = re.compile(
    r"^(10\.\d+|192\.168|172\.(1[6-9]|2\d|3[01])|169\.254|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7]))\."
)


def _ends_with_private_ipv4(numbers: list[int]) -> bool:
    return len(numbers) >= PATH_LENGTH + 4 and bool(
        _PRIVATE_IPV4.match(".".join(map(str, numbers[-4:])) + ".")
    )


def _find_mac(numbers: list[int], known: set[tuple[int, ...]]) -> int | None:
    """Start of a MAC encoded as six decimal OID components, or None.

    A MAC already seen as a value is recognised anywhere after the MIB path. Otherwise
    only the last six components are considered, and only if at least three of them are
    above 31: table, column and row numbers are almost always small, while a vendor's
    own OID path (e.g. 171.10.37.37) is never at the end of an instance OID.
    """
    for start in range(PATH_LENGTH, len(numbers) - 5):
        if tuple(numbers[start : start + 6]) in known:
            return start
    if _ends_with_private_ipv4(numbers):
        return None  # e.g. an ARP-style index "...3.192.168.1.10" is an IP, not a MAC
    tail = numbers[-6:]
    if (
        len(numbers) >= PATH_LENGTH + 6
        and all(n <= 255 for n in tail)
        and sum(n > 31 for n in tail) >= 3
    ):
        return len(numbers) - 6
    return None


def _redact_oid(oid: str, redactor: Redactor, known: set[tuple[int, ...]]) -> str:
    """Replace a MAC, or a private IPv4 address at the end, inside an OID's index."""
    parts = oid.split(".")
    if not all(p.isdigit() for p in parts):
        return oid
    numbers = [int(p) for p in parts]
    if (start := _find_mac(numbers, known)) is not None:
        mac = redactor.mac(numbers[start : start + 6])
        return ".".join([*parts[:start], f"[{mac}]", *parts[start + 6 :]])
    if _ends_with_private_ipv4(numbers):
        return ".".join([*parts[:-4], f"[{parts[-4]}.x.x.{parts[-1]}]"])
    return oid


# SNMPv3 protocols (--snmp-auth / --snmp-priv) -> pysnmp names.
SNMP_AUTH = {
    "md5": "usmHMACMD5AuthProtocol",
    "sha": "usmHMACSHAAuthProtocol",
    "sha224": "usmHMAC128SHA224AuthProtocol",
    "sha256": "usmHMAC192SHA256AuthProtocol",
    "sha384": "usmHMAC256SHA384AuthProtocol",
    "sha512": "usmHMAC384SHA512AuthProtocol",
}
SNMP_PRIV = {
    "des": "usmDESPrivProtocol",
    "3des": "usm3DESEDEPrivProtocol",
    "aes": "usmAesCfb128Protocol",
    "aes192": "usmAesCfb192Protocol",
    "aes256": "usmAesCfb256Protocol",
    # Cisco's AES-192/256 key extension
    "aes192c": "usmAesBlumenthalCfb192Protocol",
    "aes256c": "usmAesBlumenthalCfb256Protocol",
}


async def _snmp_session(args: argparse.Namespace, secrets: dict[str, str]):
    import pysnmp.hlapi.v3arch.asyncio as hlapi
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData,
        ContextData,
        SnmpEngine,
        UdpTransportTarget,
        UsmUserData,
    )

    if args.snmp_user:
        auth = UsmUserData(
            args.snmp_user,
            authKey=secrets["auth"],
            privKey=secrets["priv"],
            authProtocol=getattr(hlapi, SNMP_AUTH[args.snmp_auth]),
            privProtocol=getattr(hlapi, SNMP_PRIV[args.snmp_priv]),
        )
    else:
        auth = CommunityData(secrets["community"], mpModel=1)
    target = await UdpTransportTarget.create((args.host, args.port), timeout=3, retries=1)
    return SnmpEngine(), auth, target, ContextData()


async def collect_snmp(args: argparse.Namespace, secrets: dict[str, str]) -> list[str]:
    """Walk the system group, the vendor subtree and the standard tables."""
    from pysnmp.hlapi.v3arch.asyncio import ObjectIdentity, ObjectType, bulk_walk_cmd, get_cmd

    engine, auth, target, context = await _snmp_session(args, secrets)
    redactor = Redactor(list(secrets.values()))
    report = [f"# mode: SNMP {'v3 user' if args.snmp_user else 'v2c'}"]

    # lookupMib=False: numeric OIDs and plain types, no MIB files needed.
    error, status, _index, binds = await get_cmd(
        engine, auth, target, context,
        *(ObjectType(ObjectIdentity(oid)) for oid in SYSTEM_OIDS.values()),
        lookupMib=False,
    )
    if error or status:
        raise RuntimeError(f"SNMP request failed: {error or status.prettyPrint()}")
    system = {name: value.prettyPrint() for name, (_oid, value) in zip(SYSTEM_OIDS, binds)}
    report.append("# --- system ---")
    for name, value in system.items():
        # sysObjectID is an OID (it would look like an IP address to the redaction).
        shown = value if args.no_redact or name in ("sysObjectID", "sysUpTime") else redactor.text(value)
        report.append(f"{name} = {shown}")

    roots: dict[str, str] = {}
    object_id = system.get("sysObjectID", "")
    if object_id.startswith(ENTERPRISE_PREFIX):
        number = int(object_id[len(ENTERPRISE_PREFIX) :].split(".")[0])
        vendor = KNOWN_ENTERPRISES.get(number, "unknown vendor")
        report.append(f"# enterprise number: {number} ({vendor})")
        roots |= VENDOR_EXTRA_ROOTS.get(number, {})
        roots[f"vendor subtree ({vendor})"] = f"{ENTERPRISE_PREFIX}{number}"
    else:
        report.append("# sysObjectID has no enterprise number; walking standard MIBs only")
    roots |= STANDARD_ROOTS
    roots |= {f"--snmp-root {oid}": oid for oid in args.snmp_root or []}

    rows: list[tuple[str, str, str]] = []  # (section, oid, formatted value)
    for section, root in roots.items():
        count = 0
        walker = bulk_walk_cmd(
            engine, auth, target, context, 0, 25,
            ObjectType(ObjectIdentity(root)), lexicographicMode=False, lookupMib=False,
        )
        async for error, status, _index, binds in walker:
            if error or status:
                rows.append((section, root, f"# walk stopped: {error or status.prettyPrint()}"))
                break
            for oid, value in binds:
                rows.append((section, str(oid), _format_value(value)))
                count += 1
            if count >= args.snmp_max:
                rows.append((section, root, f"# stopped after {count} values (--snmp-max)"))
                break
        if count == 0:
            rows.append((section, root, f"# no values under {root}"))

    # MACs seen as values help recognise the same MACs inside other tables' indexes.
    # All-zero and broadcast MACs are placeholders in empty tables, not clients.
    placeholders = {"MAC: 00:00:00:00:00:00", "MAC: FF:FF:FF:FF:FF:FF"}
    known = {
        tuple(int(h, 16) for h in value[5:].split(":"))
        for _s, _o, value in rows
        if value.startswith("MAC: ") and value not in placeholders
    }
    # Likely client tables: columns whose values are MACs or whose index holds a MAC.
    tables: Counter[str] = Counter()
    for _s, oid, value in rows:
        if value.startswith("# "):
            continue
        numbers = [int(p) for p in oid.split(".") if p.isdigit()]
        start = _find_mac(numbers, known)
        if start is not None:
            tables[".".join(map(str, numbers[:start]))] += 1
        elif value.startswith("MAC: ") and value not in placeholders:
            tables[".".join(map(str, numbers[:-1])) + "  (index not decoded)"] += 1
    report.append("# --- likely client tables (columns with MACs in values or index) ---")
    if tables:
        report.extend(f"{column}  ({count} rows)" for column, count in tables.most_common(30))
    else:
        report.append("# none found: the client list may not be available over SNMP")

    # Text in a vendor walk can be anything, including a Wi-Fi passphrase that no
    # pattern recognises. So unless --snmp-full-values is given, text and binary values
    # are shown in full only inside the likely client tables (SSID names, MACs there are
    # what a driver needs); elsewhere only their type and length.
    client_entries = {
        column.split()[0].rsplit(".", 1)[0] + "." for column in tables
    }
    if not args.snmp_full_values:
        report.append(
            "# text values outside the client tables are shown as their length only;"
            " --snmp-full-values shows them (review before sharing)"
        )

    section = None
    for row_section, oid, value in rows:
        if row_section != section:
            section = row_section
            report.append(f"# --- {section} ---")
        if value.startswith("# ") or args.no_redact:
            report.append(value if value.startswith("# ") else f"{oid} = {value}")
            continue
        if not (args.snmp_full_values or oid.startswith(tuple(client_entries))):
            value = _elide(value)
        # OID-typed values look like IP addresses to the text redaction; keep them.
        shown = value if value.startswith("ObjectIdentifier: ") else redactor.text(value)
        report.append(f"{_redact_oid(oid, redactor, known)} = {shown}")
    return report


def _elide(value: str) -> str:
    """STRING/HEX values reduced to their length; other types unchanged."""
    if value.startswith("STRING: "):
        text = value[len("STRING: ") :]
        length = len(text) - 2  # repr quotes
        return "STRING: ''" if length <= 0 else f"STRING({length} chars)"
    if value.startswith("HEX: "):
        return f"HEX({len(value[len('HEX: '):].split())} bytes)"
    return value


def run_snmp(args: argparse.Namespace) -> str | None:
    """SNMP mode; returns the report, or None after printing an error."""
    try:
        import pysnmp  # noqa: F401
    except ImportError:
        print("SNMP mode needs pysnmp: pip install pysnmp", file=sys.stderr)
        return None
    if args.snmp_user:
        auth = os.environ.get("AP_PASSWORD") or getpass.getpass("SNMPv3 auth key: ")
        priv = os.environ.get("AP_PRIV_KEY") or getpass.getpass(
            "SNMPv3 privacy key (empty = same as auth): "
        ) or auth
        secrets = {"auth": auth, "priv": priv}
    else:
        secrets = {
            "community": os.environ.get("AP_COMMUNITY")
            or getpass.getpass("SNMP community (read-only): ")
        }
    try:
        return "\n".join(asyncio.run(collect_snmp(args, secrets)))
    except Exception as err:  # report plainly, never a traceback dump
        print(f"SNMP collection failed: {err!r}", file=sys.stderr)
        print("Check host, community/user, and that SNMP is enabled on the AP.", file=sys.stderr)
        return None


# --- main ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--profile", default="generic", choices=sorted(PROFILES))
    parser.add_argument("--command", action="append")
    parser.add_argument("--legacy-ssh", action="store_true")
    parser.add_argument("--snmp", action="store_true")
    parser.add_argument("--snmp-user")
    parser.add_argument("--snmp-auth", default="sha", choices=sorted(SNMP_AUTH))
    parser.add_argument("--snmp-priv", default="aes", choices=sorted(SNMP_PRIV))
    parser.add_argument("--snmp-root", action="append")
    parser.add_argument("--snmp-max", type=int, default=20000)
    parser.add_argument("--snmp-full-values", action="store_true")
    parser.add_argument("--no-redact", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--list-profiles", action="store_true")
    args = parser.parse_args()

    if args.list_profiles:
        for name, commands in PROFILES.items():
            print(f"{name}: {', '.join(commands)}")
        return 0
    if not args.host:
        parser.error("--host is required")
    if args.port is None:
        args.port = 161 if args.snmp else 22

    try:
        report = run_snmp(args) if args.snmp else run_ssh(args)
    except Exception as err:  # report anything else plainly, never a traceback dump
        print(f"Unexpected error: {err!r}", file=sys.stderr)
        return 1
    if report is None:
        return 1

    output = args.output or f"ap-report-{args.host.replace(':', '_')}.txt"
    with open(output, "w", encoding="utf-8") as fh:
        fh.write(REVIEW_WARNING + report + "\n")
    print(f"Report written to {output}.")
    print(
        "READ IT BEFORE SHARING: automatic redaction can't recognise everything (e.g. a\n"
        "Wi-Fi passphrase, names, locations, serial numbers). Replace anything private\n"
        "with <redacted> by hand, then attach it to the issue."
    )
    return 0


REVIEW_WARNING = """\
# ==================================================================================
# READ THIS REPORT BEFORE SHARING IT, AND REDACT BY HAND WHAT IS STILL PRIVATE.
#
# Removed automatically: MAC addresses (vendor prefix kept), IPv4 and email
# addresses, key-like hex strings, the password/community/keys you typed, and
# settings whose name looks secret. It can NOT recognise everything: a Wi-Fi
# passphrase, user or host names, locations, serial numbers or anything else that
# looks like ordinary text may still be here.
#
# Read every line. Replace anything you don't want public with <redacted>; the
# structure (commands, OIDs, column layout) is what matters for adding support.
# Delete this report when you no longer need it.
# ==================================================================================
"""


if __name__ == "__main__":
    sys.exit(main())
