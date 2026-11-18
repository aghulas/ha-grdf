"""Fixtures for the Home Assistant side (pytest-homeassistant-custom-component)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from .common import NOW, OLDEST, YESTERDAY, FakeClient


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Any,  # first: the recorder must be set up before `hass`
    enable_custom_integrations: None,
) -> Iterator[None]:
    yield


@pytest.fixture
async def grdf(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> AsyncIterator[FakeClient]:
    """Paris time, a frozen clock, and a fake GRDF client serving 15 days of data."""
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to(NOW)
    client = FakeClient()
    client.fill(OLDEST - timedelta(days=5), YESTERDAY)
    with patch("custom_components.grdf_conso.GrdfClient", return_value=client) as factory:
        client.factory = factory
        yield client
