"""Fixtures for the Home Assistant side (pytest-homeassistant-custom-component)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Any,  # first: the recorder must be set up before `hass`
    enable_custom_integrations: None,
) -> Iterator[None]:
    yield
