"""Read-only access to the GRDF customer portal (no Home Assistant dependency)."""

from .client import GrdfClient
from .const import GrdfUrls
from .errors import GrdfAuthError, GrdfError, GrdfRouteNotAllowed, GrdfTemporaryError
from .models import (
    Meter,
    Reading,
    degree_days,
    parse_daily,
    parse_meters,
    parse_published,
    parse_temperatures,
)

__all__ = [
    "GrdfAuthError",
    "GrdfClient",
    "GrdfError",
    "GrdfRouteNotAllowed",
    "GrdfTemporaryError",
    "GrdfUrls",
    "Meter",
    "Reading",
    "degree_days",
    "parse_daily",
    "parse_meters",
    "parse_published",
    "parse_temperatures",
]
