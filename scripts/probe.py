#!/usr/bin/env python3
"""Moved: this tool is now part of the wifi-ap-associations library.

    pip install wifi-ap-associations
    wifi-ap-probe --help

This file only forwards to it, so old instructions keep working once the library is
installed.
"""

import sys

try:
    from wifi_ap_associations.probe import main
except ImportError:
    print(
        "This tool moved to the wifi-ap-associations library:\n"
        "    pip install wifi-ap-associations\n"
        "    wifi-ap-probe --help",
        file=sys.stderr,
    )
    sys.exit(1)

sys.exit(main())
