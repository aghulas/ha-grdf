"""Transport rules of the client: allow-list, spacing, single retry, session expiry, back-off."""

from __future__ import annotations

from datetime import date

import aiohttp
import pytest

from custom_components.grdf_conso.api import (
    GrdfClient,
    GrdfRouteNotAllowed,
    GrdfTemporaryError,
)
from custom_components.grdf_conso.api.client import check_path

from .conftest import PCE, FakeGrdf, daily_row, make_client

WHOAMI = "/api/e-connexion/users/whoami"
METERS = "/api/e-accred/accreditations"
DAILY = "/api/e-conso/pce/consommation/informatives"


async def test_first_call_logs_in_once_and_reads(fake: FakeGrdf, client: GrdfClient) -> None:
    meters = await client.async_get_meters()
    assert [(m.pce, m.alias, m.remote_reading) for m in meters] == [(PCE, "Site de test", True)]
    await client.async_get_meters()
    assert fake.password_posts == 1
    assert fake.api_calls == [METERS, METERS]
    assert all(method == "GET" for method, path, _ in fake.calls if path.startswith("/api/"))


async def test_data_calls(fake: FakeGrdf, client: GrdfClient) -> None:
    fake.payloads[DAILY] = {
        PCE: {"releves": [daily_row("2025-12-31", 5), daily_row("2026-01-01", 120.5, 10.7)]}
    }
    fake.payloads["/api/e-conso/pce/consommation/publiees"] = []
    fake.payloads[f"/api/e-conso/pce/{PCE}/meteo"] = {"2026-01-01": 3.5, "2026-01-02": 20}

    daily = await client.async_get_daily(PCE, date(2026, 1, 1), date(2026, 1, 2))
    assert [(r.gas_day, r.energy_kwh, r.volume_m3) for r in daily] == [
        (date(2026, 1, 1), 120.5, 10.7)
    ]
    assert fake.queries[-1] == {
        "dateDebut": "2026-01-01",
        "dateFin": "2026-01-02",
        "pceList[]": PCE,
    }
    assert await client.async_get_published(PCE, date(2025, 1, 1), date(2026, 1, 1)) == []
    temps = await client.async_get_temperatures(PCE, date(2026, 1, 2), 2)
    assert temps == {date(2026, 1, 1): 3.5, date(2026, 1, 2): 20.0}
    assert fake.queries[-1] == {"dateFinPeriode": "2026-01-02", "nbJours": "2"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/e-accred/accreditations/123",
        "/api/e-conso/pce/consommation/informatives/telecharger",
        "/api/e-conso/pce",
        "/api/e-connexion/users/whoami/../../x",
        f"/api/e-conso/pce/{PCE}/autre",
        "/api/e-conso/pce/123/meteo",
        "/",
    ],
)
def test_allow_list_rejects(path: str) -> None:
    with pytest.raises(GrdfRouteNotAllowed) as err:
        check_path(path)
    assert PCE not in str(err.value)


def test_allow_list_accepts() -> None:
    for path in (
        WHOAMI,
        METERS,
        DAILY,
        f"/api/e-conso/pce/{PCE}/meteo",
        "/api/e-conso/pce/GI123456/details",
    ):
        check_path(path)


async def test_disallowed_path_and_bad_pce_send_nothing(fake: FakeGrdf, client: GrdfClient) -> None:
    with pytest.raises(GrdfRouteNotAllowed):
        await client._get("/api/e-accred/accreditations/ajout")
    for bad in ("123", "../x", f"{PCE}/details"):
        with pytest.raises(ValueError, match="invalid PCE"):
            await client.async_get_temperatures(bad, date(2026, 1, 1), 1)
        with pytest.raises(ValueError, match="invalid PCE"):
            await client.async_get_daily(bad, date(2026, 1, 1), date(2026, 1, 1))
    assert fake.calls == []


async def test_calls_are_spaced(fake: FakeGrdf, http: aiohttp.ClientSession) -> None:
    client = make_client(fake, http, min_interval=0.15)
    await client.async_get_meters()
    await client.async_get_meters()
    await client.async_get_meters()
    stamps = [t for _, path, t in fake.calls if path.startswith("/api/")]
    assert len(stamps) == 3
    assert all(later - earlier >= 0.14 for earlier, later in zip(stamps, stamps[1:], strict=False))


async def test_html_glitch_is_retried_once(fake: FakeGrdf, client: GrdfClient) -> None:
    await client.async_login()
    fake.html_glitches = 1
    assert len(await client.async_get_meters()) == 1
    assert fake.api_calls == [METERS, METERS]


async def test_persistent_glitch_with_valid_session_backs_off(
    fake: FakeGrdf, client: GrdfClient
) -> None:
    await client.async_login()
    fake.html_glitches = 2
    with pytest.raises(GrdfTemporaryError, match="session is valid"):
        await client.async_get_meters()
    assert fake.api_calls == [METERS, METERS, WHOAMI]  # one retry, one session check, no more
    assert fake.password_posts == 1


async def test_expired_session_logs_in_again_once(fake: FakeGrdf, client: GrdfClient) -> None:
    await client.async_get_meters()
    fake.expire_sessions()
    assert len(await client.async_get_meters()) == 1
    assert fake.password_posts == 2
    assert fake.api_calls == [METERS, METERS, METERS, WHOAMI, METERS]


async def test_session_lost_right_after_login_does_not_login_again(
    fake: FakeGrdf, http: aiohttp.ClientSession
) -> None:
    client = make_client(fake, http, min_relogin_interval=600)
    await client.async_get_meters()
    fake.expire_sessions()
    with pytest.raises(GrdfTemporaryError, match="shortly after"):
        await client.async_get_meters()
    assert fake.password_posts == 1
    assert client.auth_error is None


async def test_waf_403_page_backs_off_without_retry(fake: FakeGrdf, client: GrdfClient) -> None:
    await client.async_login()
    fake.waf = True
    with pytest.raises(GrdfTemporaryError, match="403"):
        await client.async_get_meters()
    assert fake.api_calls == [METERS]
    assert fake.password_posts == 1


async def test_waf_during_session_check_backs_off(fake: FakeGrdf, client: GrdfClient) -> None:
    await client.async_login()
    fake.html_glitches = 2

    async def waf_after_glitches() -> None:
        fake.waf = True

    original = client._session_alive

    async def alive() -> bool:
        await waf_after_glitches()
        return await original()

    client._session_alive = alive  # type: ignore[method-assign]
    with pytest.raises(GrdfTemporaryError, match="unavailable"):
        await client.async_get_meters()
    assert fake.password_posts == 1


async def test_http_error_with_json_is_temporary(fake: FakeGrdf, client: GrdfClient) -> None:
    await client.async_login()
    fake.server_error = True
    with pytest.raises(GrdfTemporaryError, match="HTTP 500"):
        await client.async_get_temperatures(PCE, date(2026, 1, 1), 1)
    assert fake.count(f"/api/e-conso/pce/{PCE}/meteo") == 1


async def test_network_error_is_temporary_and_masks_the_pce(
    fake: FakeGrdf, http: aiohttp.ClientSession
) -> None:
    client = make_client(fake, http)
    await client.async_login()
    client._urls = type(client._urls)(portal="http://127.0.0.1:9", okta=fake.okta)
    with pytest.raises(GrdfTemporaryError) as err:
        await client.async_get_temperatures(PCE, date(2026, 1, 1), 1)
    assert PCE not in str(err.value)
    assert "<pce>" in str(err.value)
