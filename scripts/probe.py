#!/usr/bin/env python3
"""Read one access point's association table and print it (no Home Assistant needed).

Usage:
    python3 scripts/probe.py --type dlink_dap_ssh --host 192.168.1.231 --username admin
    python3 scripts/probe.py --list-types

The password is asked for interactively (or taken from $AP_PASSWORD).
Requires: pip install wifi-ap-associations
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
from pathlib import Path
import sys

INTEGRATION_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "wifi_association_presence"
)
sys.path.insert(0, str(INTEGRATION_DIR))

from wifi_ap_associations import DRIVERS, AccessPointError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--type", default="dlink_dap_ssh", help="driver type")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--username")
    parser.add_argument("--list-types", action="store_true")
    args = parser.parse_args()

    if args.list_types:
        for type_, cls in DRIVERS.items():
            print(f"{type_:20} {cls.NAME}")
        return 0
    if args.type not in DRIVERS:
        parser.error(f"unknown type {args.type!r}; see --list-types")
    if not args.host:
        parser.error("--host is required")

    driver_cls = DRIVERS[args.type]
    config: dict[str, object] = {}
    for field in driver_cls.FIELDS:
        value = getattr(args, field.key, None)
        if value is None and field.secret:
            value = os.environ.get("AP_PASSWORD") or getpass.getpass(f"{field.key}: ")
        config[field.key] = value if value is not None else field.default

    try:
        clients = asyncio.run(driver_cls(config).async_get_associated_clients())
    except AccessPointError as err:
        print(f"FAILED: {err}", file=sys.stderr)
        return 1

    print(f"{len(clients)} associated client(s) on {args.host}")
    print(f"{'MAC':17}  {'band':6}  {'signal':>6}  {'connected':>9}  ssid")
    for c in sorted(clients, key=lambda c: (c.band or "", c.ssid or "", c.mac)):
        print(
            f"{c.mac:17}  {c.band or '?':6}  {c.signal if c.signal is not None else '?':>6}"
            f"  {c.connected_seconds if c.connected_seconds is not None else '?':>8}s"
            f"  {c.ssid or '?'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
