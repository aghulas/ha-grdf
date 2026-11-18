"""Constants of the GRDF customer portal API (see docs/api.md)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class GrdfUrls:
    """Base URLs; overridable so tests can point the client at a local fake."""

    portal: str = "https://monespace.grdf.fr"
    okta: str = "https://connexion.grdf.fr"


PATH_WHOAMI: Final = "/api/e-connexion/users/whoami"
PATH_METERS: Final = "/api/e-accred/accreditations"
PATH_DAILY: Final = "/api/e-conso/pce/consommation/informatives"
PATH_PUBLISHED: Final = "/api/e-conso/pce/consommation/publiees"
PATH_OKTA_IDENTIFY: Final = "/idp/idx/identify"
PATH_OKTA_ANSWER: Final = "/idp/idx/challenge/answer"

PCE_PATTERN: Final = r"(?:\d{14}|GI\d{6})"
PCE_RE: Final = re.compile(PCE_PATTERN)

# Read-only allow-list (CLAUDE.md rule 4): the client refuses any other portal path. GET only.
ALLOWED_PATHS: Final = (
    re.compile(r"/api/e-connexion/users/whoami"),
    re.compile(r"/api/e-accred/accreditations"),
    re.compile(r"/api/e-conso/pce/consommation/(?:informatives|publiees)"),
    re.compile(rf"/api/e-conso/pce/{PCE_PATTERN}/(?:details|meteo)"),
)

# Same User-Agent as the supervised proof of 2026-11-18 (docs/api.md §Authentication).
USER_AGENT: Final = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
PAGE_HEADERS: Final = {"User-Agent": USER_AGENT}
IDX_HEADERS: Final = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json; okta-version=1.0.0",
    "Content-Type": "application/json",
}

MIN_INTERVAL: Final = 1.0  # seconds between two HTTP calls (bursts get HTML instead of JSON)
RETRY_DELAY: Final = 3.0  # wait before the single retry of an unexpected answer
MIN_RELOGIN_INTERVAL: Final = 600.0  # never re-login automatically more often than this
REQUEST_TIMEOUT: Final = 60.0
