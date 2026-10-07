#!/usr/bin/env python3
"""Collect read-only information from an access point to help add support for it.

Logs into the AP's SSH console, runs a list of read-only commands, redacts MAC and
IP addresses and anything that looks secret, and writes a report you can attach to
a GitHub issue ("New access point model"). Review the report before sharing it.

Usage:
    python3 scripts/collect.py --host 192.0.2.10 --username admin
    python3 scripts/collect.py --host 192.0.2.10 --username admin --profile dlink_dap
    python3 scripts/collect.py --host 192.0.2.10 --username admin --command "show wireless clients"

Options:
    --profile NAME     a known command list (see --list-profiles); default: generic
    --command CMD      extra command to run (repeatable)
    --legacy-ssh       allow old algorithms (diffie-hellman-group1/14-sha1, ssh-rsa, CBC ciphers)
    --no-redact        keep MAC/IP addresses (only for your own debugging; don't share)
    --output FILE      report file (default: ap-report-<host>.txt)

Requires: pip install asyncssh
The password is asked for interactively (or taken from $AP_PASSWORD).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sys
import time
import warnings

import asyncssh

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
    r"(pass(word|phrase)?|secret|psk|key|community|token|credential)", re.IGNORECASE
)
_MAC = re.compile(r"\b([0-9A-Fa-f]{2})([:-])([0-9A-Fa-f]{2})\2([0-9A-Fa-f]{2})(?:\2[0-9A-Fa-f]{2}){3}\b")
_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
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


def _split_setting(line: str) -> tuple[str, str, str]:
    """Split "name: value" / "name=value" at the first separator ("", "", line if none)."""
    match = re.search(r"[:=]", line)
    if not match:
        return line, "", ""
    return line[: match.start()], match.group(0), line[match.end() :]


def redact(text: str) -> str:
    """Hide MAC addresses (vendor prefix kept), IPv4 addresses and secret-looking lines."""
    macs: dict[str, str] = {}

    def mac_sub(match: re.Match[str]) -> str:
        full = match.group(0).upper()
        if full not in macs:
            sep = match.group(2)
            macs[full] = sep.join([match.group(1), match.group(3), match.group(4)]).upper() + (
                f"{sep}XX{sep}XX{sep}{len(macs) + 1:02d}"
            )
        return macs[full]

    lines = []
    for line in text.splitlines():
        # Judge by the setting's name (before ":"/"="), so "auth:WPA2-PSK" stays readable.
        name, sep, _value = _split_setting(line)
        if sep and _SECRET_LINE.search(name):
            line = f"{name}{sep} <redacted>"
        line = _MAC.sub(mac_sub, line)
        line = _IPV4.sub(lambda m: f"{m.group(1)}.x.x.{m.group(4)}", line)
        lines.append(line)
    return "\n".join(lines)


async def read_until_prompt(process: asyncssh.SSHClientProcess, timeout: float) -> str:
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


async def collect(args: argparse.Namespace, password: str) -> str:
    """Log in, run the commands, return the raw transcript."""
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
                transcript.append(await read_until_prompt(process, 20))
    return "\n".join(transcript)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--profile", default="generic", choices=sorted(PROFILES))
    parser.add_argument("--command", action="append")
    parser.add_argument("--legacy-ssh", action="store_true")
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

    password = os.environ.get("AP_PASSWORD") or getpass.getpass(f"{args.host} password: ")
    try:
        transcript = asyncio.run(collect(args, password))
    except asyncssh.PermissionDenied:
        print("Login rejected. Check username/password.", file=sys.stderr)
        return 1
    except (OSError, asyncssh.Error) as err:
        print(f"Connection failed: {err!r}", file=sys.stderr)
        if "no matching" in str(err).lower() or "algorithm" in str(err).lower():
            print("Try again with --legacy-ssh.", file=sys.stderr)
        return 1
    except Exception as err:  # report anything else plainly, never a traceback dump
        print(f"Unexpected error: {err!r}", file=sys.stderr)
        return 1

    report = transcript if args.no_redact else redact(transcript)
    output = args.output or f"ap-report-{args.host.replace(':', '_')}.txt"
    with open(output, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(f"Report written to {output}. Review it before attaching it to an issue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
