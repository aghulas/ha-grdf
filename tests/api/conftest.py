"""A local fake of the GRDF portal and of its Okta tenant. Everything here is synthetic."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.grdf_conso.api import GrdfClient, GrdfUrls

USERNAME = "compte@exemple.invalid"
PASSWORD = 'synth "pass\\word" é'  # quotes and a backslash on purpose
PCE = "00000000000001"
HTML = "<html><body>Erreur</body></html>"

METERS = [
    {
        "pce": PCE,
        "alias": "Site de test",
        "teleReleve": True,
        "frequenceReleve": "MM",
        "etat": "Active",
        "nomTitulaire": "TITULAIRE SYNTH",
    }
]


def daily_row(day: str, kwh: float | None, m3: float | None = None) -> dict[str, Any]:
    return {
        "journeeGaziere": day,
        "dateDebutReleve": f"{day}T06:00:00+01:00",
        "dateFinReleve": None,
        "energieConsomme": kwh,
        "volumeBrutConsomme": m3,
        "indexDebut": 1000,
        "indexFin": 1010,
        "pcs": "11.2",
        "qualificationReleve": "Mesuré",
        "natureReleve": "Informative Journalier",
    }


class FakeGrdf:
    """State shared by the fake portal and the fake Okta."""

    def __init__(self) -> None:
        self.portal = ""
        self.okta = ""
        self.calls: list[tuple[str, str, float]] = []
        self.queries: list[dict[str, str]] = []
        self.password_posts = 0
        self.accepted_password = PASSWORD
        self.identify_mode = "password"  # | "mfa" | "refused" | "html"
        self.answer_mode = "normal"  # | "drop" | "bad_href"
        self.login_page_ok = True
        self.tokens: set[str] = set()
        self.html_glitches = 0
        self.waf = False
        self.server_error = False
        self.payloads: dict[str, Any] = {
            "/api/e-connexion/users/whoami": {"id": 1},
            "/api/e-accred/accreditations": METERS,
        }

    def expire_sessions(self) -> None:
        self.tokens.clear()

    def count(self, path: str) -> int:
        return sum(1 for _, p, _ in self.calls if p == path)

    @property
    def api_calls(self) -> list[str]:
        return [p for _, p, _ in self.calls if p.startswith("/api/")]

    # ---- portal ----

    async def home(self, request: web.Request) -> web.StreamResponse:
        self._record(request)
        if request.cookies.get("auth_token") in self.tokens:
            return web.Response(text="<html>accueil</html>", content_type="text/html")
        raise web.HTTPFound(self.okta + "/oauth2/aus0synth/v1/authorize?state=s&nonce=n")

    async def codexch(self, request: web.Request) -> web.StreamResponse:
        self._record(request)
        token = f"tok-{len(self.calls)}"
        self.tokens.add(token)
        resp = web.HTTPFound(self.portal + "/")
        resp.set_cookie("auth_token", token, httponly=True)
        raise resp

    async def api(self, request: web.Request) -> web.StreamResponse:
        self._record(request)
        self.queries.append(dict(request.query))
        if self.waf:
            return web.Response(status=403, text=HTML, content_type="text/html")
        if self.html_glitches > 0:
            self.html_glitches -= 1
            return web.Response(text=HTML, content_type="text/html")
        if request.cookies.get("auth_token") not in self.tokens:
            raise web.HTTPFound(self.portal + "/")
        if self.server_error:
            return web.json_response({"message": "boom"}, status=500)
        return web.json_response(self.payloads.get(request.path, {}))

    # ---- okta ----

    async def authorize(self, request: web.Request) -> web.StreamResponse:
        self._record(request)
        body = (
            'var oktaData = {"stateToken":"st\\x2Dsynth\\x2D1"};'
            if self.login_page_ok
            else "<html>maintenance</html>"
        )
        return web.Response(text=body, content_type="text/html")

    async def identify(self, request: web.Request) -> web.StreamResponse:
        self._record(request)
        data = await request.json()
        assert data == {"identifier": USERNAME, "stateHandle": "st-synth-1"}
        if self.identify_mode == "html":
            return web.Response(text=HTML, content_type="text/html")
        if self.identify_mode == "refused":
            return web.json_response(_messages("errors.E0000004"), status=401)
        if self.identify_mode == "mfa":
            return web.json_response(
                {
                    "stateHandle": "handle-2",
                    "remediation": {"value": [{"name": "select-authenticator-authenticate"}]},
                }
            )
        return web.json_response(
            {
                "stateHandle": "handle-2",
                "remediation": {"value": [{"name": "challenge-authenticator"}]},
                "currentAuthenticatorEnrollment": {"value": {"type": "password"}},
            }
        )

    async def answer(self, request: web.Request) -> web.StreamResponse:
        self._record(request)
        self.password_posts += 1
        data = await request.json()
        assert data["stateHandle"] == "handle-2"
        if self.answer_mode == "drop":
            assert request.transport is not None
            request.transport.close()
            return web.Response()
        if data["credentials"]["passcode"] != self.accepted_password:
            return web.json_response(_messages("errors.E0000004"), status=401)
        base = "https://ailleurs.invalid" if self.answer_mode == "bad_href" else self.okta
        return web.json_response({"success": {"href": base + "/login/token/redirect?stateToken=x"}})

    async def token_redirect(self, request: web.Request) -> web.StreamResponse:
        self._record(request)
        raise web.HTTPFound(self.portal + "/_codexch?code=c&state=s")

    def _record(self, request: web.Request) -> None:
        self.calls.append((request.method, request.path, time.monotonic()))


def _messages(key: str) -> dict[str, Any]:
    return {
        "messages": {
            "value": [{"message": "Authentication failed", "i18n": {"key": key}, "class": "ERROR"}]
        }
    }


@pytest.fixture
async def fake(socket_enabled: None) -> AsyncIterator[FakeGrdf]:
    # socket_enabled: pytest-homeassistant-custom-component blocks sockets; the fake is local.
    state = FakeGrdf()
    portal_app = web.Application()
    portal_app.router.add_get("/", state.home)
    portal_app.router.add_get("/_codexch", state.codexch)
    portal_app.router.add_route("*", "/api/{tail:.*}", state.api)
    okta_app = web.Application()
    okta_app.router.add_get("/oauth2/aus0synth/v1/authorize", state.authorize)
    okta_app.router.add_post("/idp/idx/identify", state.identify)
    okta_app.router.add_post("/idp/idx/challenge/answer", state.answer)
    okta_app.router.add_get("/login/token/redirect", state.token_redirect)
    portal, okta = TestServer(portal_app), TestServer(okta_app)
    await portal.start_server()
    await okta.start_server()
    state.portal = str(portal.make_url("")).rstrip("/")
    state.okta = str(okta.make_url("")).rstrip("/")
    try:
        yield state
    finally:
        await portal.close()
        await okta.close()


@pytest.fixture
async def http(socket_enabled: None) -> AsyncIterator[aiohttp.ClientSession]:
    # unsafe=True: the fake runs on an IP address.
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        yield session


def make_client(
    fake: FakeGrdf, http: aiohttp.ClientSession, password: str = PASSWORD, **kwargs: Any
) -> GrdfClient:
    options: dict[str, Any] = {"min_interval": 0, "retry_delay": 0, "min_relogin_interval": 0}
    options.update(kwargs)
    urls = GrdfUrls(portal=fake.portal, okta=fake.okta)
    return GrdfClient(http, USERNAME, password, urls=urls, **options)


@pytest.fixture
def client(fake: FakeGrdf, http: aiohttp.ClientSession) -> GrdfClient:
    return make_client(fake, http)


def dumps(obj: Any) -> str:
    return json.dumps(obj)
