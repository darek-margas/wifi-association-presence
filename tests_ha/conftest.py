"""Home Assistant-level tests: the integration set up in a real (test) Home Assistant.

Kept apart from tests/ because they need a full Home Assistant install:

    pip install pytest-homeassistant-custom-component
    python3 -m pytest tests_ha
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]

# Make our custom_components visible to Home Assistant's loader. With the repository
# root on the path it is usually already a namespace package covering ours; the plugin's
# own regular custom_components package (when it wins) gets ours prepended instead.
import custom_components  # noqa: E402

_OURS = str(Path(__file__).resolve().parent.parent / "custom_components")
if _OURS not in custom_components.__path__:
    try:
        custom_components.__path__.insert(0, _OURS)
    except AttributeError:  # a namespace path only appends
        custom_components.__path__.append(_OURS)


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading custom integrations in every test."""
