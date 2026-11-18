"""CLAUDE.md rule 1: a refused password is tried exactly once, then nothing is sent any more."""

from __future__ import annotations

import logging
from datetime import date

import aiohttp
import pytest

from custom_components.grdf_conso.api import GrdfAuthError, GrdfClient, GrdfTemporaryError

from .conftest import PASSWORD, PCE, USERNAME, FakeGrdf, make_client


async def _assert_blocked(client: GrdfClient, fake: FakeGrdf) -> None:
    """Every entry point now fails without a single HTTP request."""
    before = len(fake.calls)
    for call in (
        client.async_get_meters(),
        client.async_login(),
        client.async_get_daily(PCE, date(2026, 1, 1), date(2026, 1, 2)),
        client.async_get_published(PCE, date(2026, 1, 1), date(2026, 1, 2)),
        client.async_get_temperatures(PCE, date(2026, 1, 2), 2),
    ):
        with pytest.raises(GrdfAuthError) as blocked:
            await call
        assert blocked.value.reason == "blocked"
    assert len(fake.calls) == before


async def test_refused_password_is_tried_exactly_once(
    fake: FakeGrdf, http: aiohttp.ClientSession, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = make_client(fake, http, password="ancien-mot-de-passe")

    with pytest.raises(GrdfAuthError) as err:
        await client.async_get_meters()

    assert err.value.reason == "refused"
    assert err.value.status == 401
    assert err.value.okta_keys == ("errors.E0000004",)
    assert fake.password_posts == 1
    assert fake.api_calls == []
    assert client.auth_error is err.value

    await _assert_blocked(client, fake)
    assert fake.password_posts == 1

    for text in (str(err.value), caplog.text):
        assert "ancien-mot-de-passe" not in text
        assert USERNAME not in text


async def test_explicit_login_refused_blocks_too(
    fake: FakeGrdf, http: aiohttp.ClientSession
) -> None:
    client = make_client(fake, http, password="faux")
    with pytest.raises(GrdfAuthError):
        await client.async_login()
    await _assert_blocked(client, fake)
    assert fake.password_posts == 1


async def test_mfa_or_other_step_never_sends_the_password(
    fake: FakeGrdf, client: GrdfClient
) -> None:
    fake.identify_mode = "mfa"
    with pytest.raises(GrdfAuthError) as err:
        await client.async_get_meters()
    assert err.value.reason == "password_not_offered"
    assert fake.password_posts == 0
    await _assert_blocked(client, fake)


async def test_identify_refused_never_sends_the_password(
    fake: FakeGrdf, client: GrdfClient
) -> None:
    fake.identify_mode = "refused"
    with pytest.raises(GrdfAuthError) as err:
        await client.async_get_meters()
    assert err.value.reason == "identify_refused"
    assert err.value.okta_keys == ("errors.E0000004",)
    assert fake.password_posts == 0
    await _assert_blocked(client, fake)


async def test_unknown_outcome_of_the_password_step_counts_as_refused(
    fake: FakeGrdf, client: GrdfClient
) -> None:
    """The connection drops after the password left: do not send it again."""
    fake.answer_mode = "drop"
    with pytest.raises(GrdfAuthError) as err:
        await client.async_get_meters()
    assert err.value.reason == "refused"
    assert fake.password_posts == 1
    await _assert_blocked(client, fake)


async def test_password_changed_elsewhere_stops_at_the_first_refused_relogin(
    fake: FakeGrdf, client: GrdfClient
) -> None:
    """The incident: the session expires and the stored password is no longer the right one."""
    assert len(await client.async_get_meters()) == 1
    fake.expire_sessions()
    fake.accepted_password = "nouveau-mot-de-passe"

    with pytest.raises(GrdfAuthError) as err:
        await client.async_get_meters()

    assert err.value.reason == "refused"
    assert fake.password_posts == 2  # the first login, then ONE refused re-login
    await _assert_blocked(client, fake)
    assert fake.password_posts == 2


async def test_unavailable_login_page_is_not_an_auth_failure(
    fake: FakeGrdf, client: GrdfClient
) -> None:
    fake.login_page_ok = False
    with pytest.raises(GrdfTemporaryError):
        await client.async_get_meters()
    assert fake.password_posts == 0
    assert client.auth_error is None

    fake.login_page_ok = True  # next day: works again, nothing was blocked
    assert len(await client.async_get_meters()) == 1


async def test_identify_glitch_is_temporary(fake: FakeGrdf, client: GrdfClient) -> None:
    fake.identify_mode = "html"
    with pytest.raises(GrdfTemporaryError):
        await client.async_login()
    assert fake.password_posts == 0
    assert client.auth_error is None


async def test_foreign_success_link_is_not_followed(fake: FakeGrdf, client: GrdfClient) -> None:
    fake.answer_mode = "bad_href"
    with pytest.raises(GrdfTemporaryError):
        await client.async_login()
    assert fake.count("/login/token/redirect") == 0


async def test_password_is_sent_verbatim(fake: FakeGrdf, client: GrdfClient) -> None:
    """Quotes and backslashes must survive (string-formatted JSON would corrupt them)."""
    assert '"' in PASSWORD and "\\" in PASSWORD
    await client.async_login()
    assert fake.password_posts == 1
