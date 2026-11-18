"""Read-only client of the GRDF customer portal.

Lockout safety (CLAUDE.md rule 1): after an authentication refusal the client is *blocked* — every
further call raises GrdfAuthError("blocked") without any HTTP request. Only a new client, built
after a user action (reauth flow), talks to GRDF again.

The aiohttp session must be dedicated to this client (its cookie jar holds the GRDF session and is
cleared before each login) — never Home Assistant's shared session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, timedelta
from typing import Any

import aiohttp

from .auth import async_login
from .const import (
    ALLOWED_PATHS,
    MIN_INTERVAL,
    MIN_RELOGIN_INTERVAL,
    PATH_DAILY,
    PATH_METERS,
    PATH_PUBLISHED,
    PATH_WHOAMI,
    PCE_PATTERN,
    PCE_RE,
    REQUEST_TIMEOUT,
    RETRY_DELAY,
    USER_AGENT,
    GrdfUrls,
)
from .errors import GrdfAuthError, GrdfRouteNotAllowed, GrdfTemporaryError
from .models import (
    Meter,
    Reading,
    parse_daily,
    parse_meters,
    parse_published,
    parse_temperatures,
)

_LOGGER = logging.getLogger(__name__)

_REDIRECTS = (301, 302, 303, 307, 308)
_PCE_IN_PATH = re.compile(PCE_PATTERN)


def check_path(path: str) -> None:
    """Raise unless `path` is in the read-only allow-list."""
    if not any(p.fullmatch(path) for p in ALLOWED_PATHS):
        raise GrdfRouteNotAllowed(f"path not allowed: {_public(path)}")


def _public(path: str) -> str:
    """A path that can be logged: PCE numbers masked."""
    return _PCE_IN_PATH.sub("<pce>", path)


class GrdfClient:
    """One GRDF account. All calls are serialized and spaced by at least `min_interval`."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        urls: GrdfUrls | None = None,
        min_interval: float = MIN_INTERVAL,
        retry_delay: float = RETRY_DELAY,
        min_relogin_interval: float = MIN_RELOGIN_INTERVAL,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._urls = urls or GrdfUrls()
        self._min_interval = min_interval
        self._retry_delay = retry_delay
        self._min_relogin_interval = min_relogin_interval
        self._lock = asyncio.Lock()
        self._last_call = float("-inf")
        self._last_login = float("-inf")
        self._logged_in = False
        self._auth_error: GrdfAuthError | None = None

    @property
    def auth_error(self) -> GrdfAuthError | None:
        """The refusal that blocked this client, if any."""
        return self._auth_error

    # ---- authentication -------------------------------------------------------------------

    async def async_login(self) -> None:
        """Log in now (config flow, reauth). One attempt; a refusal blocks the client."""
        async with self._lock:
            await self._login()

    def _raise_if_blocked(self) -> None:
        if self._auth_error is not None:
            raise GrdfAuthError(
                "blocked", self._auth_error.okta_keys, self._auth_error.status
            ) from self._auth_error

    async def _login(self) -> None:
        self._raise_if_blocked()
        self._logged_in = False
        self._last_login = asyncio.get_running_loop().time()
        self._session.cookie_jar.clear()
        try:
            await async_login(self._session, self._username, self._password, self._urls)
        except GrdfAuthError as err:
            self._auth_error = err
            _LOGGER.warning("GRDF refused the authentication (%s): polling stopped", err.reason)
            raise
        finally:
            self._last_call = asyncio.get_running_loop().time()
        self._logged_in = True

    async def _relogin(self) -> None:
        """The session expired: log in again with the stored credentials — rarely."""
        elapsed = asyncio.get_running_loop().time() - self._last_login
        if elapsed < self._min_relogin_interval:
            raise GrdfTemporaryError("session lost shortly after a login: backing off")
        await self._login()

    # ---- transport ------------------------------------------------------------------------

    async def _request(self, path: str, params: dict[str, str] | None) -> tuple[int, Any]:
        """One GET. Returns (status, JSON) or (status, None) when the body is not JSON."""
        check_path(path)
        loop = asyncio.get_running_loop()
        wait = self._last_call + self._min_interval - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with self._session.get(
                self._urls.portal + path,
                params=params,
                allow_redirects=False,  # a 30x means the session is gone
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Referer": self._urls.portal + "/entreprises/",
                },
            ) as resp:
                status, text = resp.status, await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GrdfTemporaryError(
                f"request failed ({type(err).__name__}) on {_public(path)}"
            ) from err
        finally:
            self._last_call = loop.time()
        try:
            return status, json.loads(text)
        except ValueError:
            return status, None

    @staticmethod
    def _suspect(status: int, data: Any) -> bool:
        return data is None or status in _REDIRECTS or status in (401, 403)

    async def _session_alive(self) -> bool:
        status, data = await self._request(PATH_WHOAMI, None)
        if status == 200 and data is not None:
            return True
        if status == 403 or status >= 500:
            raise GrdfTemporaryError(f"GRDF portal unavailable (HTTP {status})")
        return False

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        check_path(path)
        async with self._lock:
            self._raise_if_blocked()
            if not self._logged_in:
                await self._login()
            status, data = await self._request(path, params)
            if status == 403 and data is None:
                # Full-page "Erreur : 403 — problème technique" (WAF / incident): no retry.
                raise GrdfTemporaryError("GRDF portal answered its 403 error page: backing off")
            if data is None or status in _REDIRECTS:
                # Often a glitch (HTML instead of JSON after a burst): one retry, not more.
                await asyncio.sleep(self._retry_delay)
                status, data = await self._request(path, params)
            if self._suspect(status, data):
                if await self._session_alive():
                    raise GrdfTemporaryError(
                        f"unexpected answer (HTTP {status}) on {_public(path)} "
                        "although the session is valid"
                    )
                _LOGGER.debug("GRDF session expired: logging in again")
                await self._relogin()
                status, data = await self._request(path, params)
                if self._suspect(status, data):
                    raise GrdfTemporaryError(
                        f"unexpected answer (HTTP {status}) on {_public(path)} after a new login"
                    )
            if status >= 400:
                raise GrdfTemporaryError(f"HTTP {status} on {_public(path)}")
            return data

    # ---- data -----------------------------------------------------------------------------

    @staticmethod
    def _check_pce(pce: str) -> None:
        if not PCE_RE.fullmatch(pce):
            raise ValueError("invalid PCE identifier")

    async def async_get_meters(self) -> list[Meter]:
        """The PCE attached to the account."""
        return parse_meters(await self._get(PATH_METERS))

    async def async_get_daily(self, pce: str, start: date, end: date) -> list[Reading]:
        """Daily readings (one per gas day) within [start, end].

        GRDF selects on the end of the reading: `dateDebut`/`dateFin` return the gas days from
        `dateDebut − 1` to `dateFin − 1`. One more day is asked so that `end` is included; the
        extra day before `start` is dropped by the parser.
        """
        self._check_pce(pce)
        payload = await self._get(PATH_DAILY, self._range(pce, start, end + timedelta(days=1)))
        return parse_daily(payload, pce, start, end)

    async def async_get_published(self, pce: str, start: date, end: date) -> list[Reading]:
        """Published (billing) readings for the period."""
        self._check_pce(pce)
        payload = await self._get(PATH_PUBLISHED, self._range(pce, start, end))
        return parse_published(payload, pce)

    async def async_get_temperatures(self, pce: str, end: date, days: int) -> dict[date, float]:
        """Daily mean temperature at the site for the `days` days ending on `end`."""
        self._check_pce(pce)
        payload = await self._get(
            f"/api/e-conso/pce/{pce}/meteo",
            {"dateFinPeriode": end.isoformat(), "nbJours": str(days)},
        )
        return parse_temperatures(payload)

    @staticmethod
    def _range(pce: str, start: date, end: date) -> dict[str, str]:
        return {"dateDebut": start.isoformat(), "dateFin": end.isoformat(), "pceList[]": pce}
