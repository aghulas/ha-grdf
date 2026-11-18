"""Headless login on GRDF's Okta (Identity Engine) — see docs/api.md §Authentication.

The one rule this module exists for (CLAUDE.md rule 1): **the password is sent at most once per
call, and only when Okta is asking for a password.** There is no loop and no retry in here.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp
from yarl import URL

from .const import IDX_HEADERS, PAGE_HEADERS, PATH_OKTA_ANSWER, PATH_OKTA_IDENTIFY, GrdfUrls
from .errors import GrdfAuthError, GrdfTemporaryError

_LOGGER = logging.getLogger(__name__)

_STATE_TOKEN = re.compile(r'"stateToken"\s*:\s*"([^"]+)"')
_JS_HEX_ESCAPE = re.compile(r"\\x([0-9A-Fa-f]{2})")
_NETWORK_ERRORS = (aiohttp.ClientError, TimeoutError)


async def _json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return json.loads(await resp.text())
    except ValueError:
        return None


def _okta_keys(body: Any) -> tuple[str, ...]:
    """Message keys / error code of an Okta answer (generic identifiers, no personal data)."""
    if not isinstance(body, dict):
        return ()
    keys: list[str] = []
    messages = body.get("messages")
    for msg in messages.get("value", []) if isinstance(messages, dict) else []:
        key = (msg.get("i18n") or {}).get("key") if isinstance(msg, dict) else None
        if isinstance(key, str):
            keys.append(key)
    if isinstance(body.get("errorCode"), str):
        keys.append(body["errorCode"])
    return tuple(keys)


def _wants_password(body: dict[str, Any]) -> bool:
    remediation = body.get("remediation")
    steps = remediation.get("value", []) if isinstance(remediation, dict) else []
    names = {s.get("name") for s in steps if isinstance(s, dict)}
    if "challenge-authenticator" not in names:
        return False
    for key in ("currentAuthenticatorEnrollment", "currentAuthenticator"):
        value = body[key].get("value") if isinstance(body.get(key), dict) else None
        if isinstance(value, dict):
            return value.get("type") == "password"
    return False


async def async_login(
    session: aiohttp.ClientSession, username: str, password: str, urls: GrdfUrls
) -> None:
    """Log `session` in (its cookie jar receives the portal session cookies).

    Raises GrdfAuthError when authentication is refused or needs more than a password, and
    GrdfTemporaryError when the flow could not be run or completed for another reason. The
    password leaves this function at most once, in the single POST of step 3.
    """
    okta = URL(urls.okta)

    # 1. The portal redirects to Okta's authorize page, which embeds a state token.
    try:
        async with session.get(urls.portal + "/", headers=PAGE_HEADERS) as resp:
            html = await resp.text()
            status, landed = resp.status, resp.url
    except _NETWORK_ERRORS as err:
        raise GrdfTemporaryError(f"login page unreachable ({type(err).__name__})") from err
    match = _STATE_TOKEN.search(html)
    if status != 200 or landed.host != okta.host or match is None:
        raise GrdfTemporaryError(f"unexpected login page (HTTP {status})")
    state = _JS_HEX_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), match.group(1))

    # 2. Identifier only — not a password attempt.
    session.cookie_jar.update_cookies({"ln": username}, okta)
    try:
        async with session.post(
            urls.okta + PATH_OKTA_IDENTIFY,
            json={"identifier": username, "stateHandle": state},
            headers=IDX_HEADERS,
        ) as resp:
            status, body = resp.status, await _json(resp)
    except _NETWORK_ERRORS as err:
        raise GrdfTemporaryError(f"identify step failed ({type(err).__name__})") from err
    if status in (400, 401, 403) and isinstance(body, dict):
        raise GrdfAuthError("identify_refused", _okta_keys(body), status)
    if status != 200 or not isinstance(body, dict):
        raise GrdfTemporaryError(f"unexpected answer to the identify step (HTTP {status})")
    handle = body.get("stateHandle")
    if not isinstance(handle, str) or not _wants_password(body):
        raise GrdfAuthError("password_not_offered", _okta_keys(body), status)

    # 3. The password: ONE request. Any outcome other than a success link is a refusal —
    #    including a network error, since the password may have been received and rejected.
    try:
        async with session.post(
            urls.okta + PATH_OKTA_ANSWER,
            json={"credentials": {"passcode": password}, "stateHandle": handle},
            headers=IDX_HEADERS,
        ) as resp:
            status, body = resp.status, await _json(resp)
    except _NETWORK_ERRORS as err:
        raise GrdfAuthError("refused", ("network:" + type(err).__name__,)) from err
    success = body.get("success") if isinstance(body, dict) else None
    href = success.get("href") if isinstance(success, dict) else None
    if status != 200 or not isinstance(href, str):
        raise GrdfAuthError("refused", _okta_keys(body), status)

    # 4. Back to the portal, which sets its session cookies. The password was accepted: a failure
    #    from here on is not an authentication refusal.
    target = URL(href)
    if (target.scheme, target.host, target.port) != (okta.scheme, okta.host, okta.port):
        raise GrdfTemporaryError("unexpected redirect target after login")
    try:
        async with session.get(target, headers=PAGE_HEADERS) as resp:
            await resp.read()
            status = resp.status
    except _NETWORK_ERRORS as err:
        raise GrdfTemporaryError(f"post-login redirect failed ({type(err).__name__})") from err
    if status != 200:
        raise GrdfTemporaryError(f"post-login redirect failed (HTTP {status})")
    _LOGGER.debug("GRDF login completed")
