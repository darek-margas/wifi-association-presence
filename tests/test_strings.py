"""Every driver setting has a label in the forms, and every listed value a name."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wifi_ap_associations import DRIVERS

COMPONENT = Path(__file__).parent.parent / "custom_components" / "wifi_association_presence"


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json"])
def test_every_driver_field_is_labelled(name: str) -> None:
    strings = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
    steps = strings["config_subentries"]["access_point"]["step"]
    selectors = strings["selector"]
    for type_, cls in DRIVERS.items():
        for field in cls.FIELDS:
            for step in ("details", "reconfigure"):
                assert field.key in steps[step]["data"], (name, type_, step, field.key)
            if field.choices:
                options = selectors[field.key]["options"]
                assert set(field.choices) <= set(options), (name, type_, field.key)
