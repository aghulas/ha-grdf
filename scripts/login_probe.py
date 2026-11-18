#!/usr/bin/env python3
"""Supervised, one-shot probe of the headless GRDF login (Okta Identity Engine).

Run by hand, by the account owner, in a terminal:

    .venv/bin/python scripts/login_probe.py

Safety (CLAUDE.md rule 1):
- the password is typed at a prompt (never an argument, an env var or a file) and sent AT MOST ONCE;
- no loop, no retry anywhere;
- a sentinel file is written before the password leaves and removed only on success: after a refusal
  (or an unknown outcome) the script refuses to run again until the owner deletes it by hand;
- the password is only sent if Okta is actually asking for a password.

Output: a sanitized report (HTTP statuses, JSON key names, Okta message keys, cookie names) printed and
written to captures/login-probe.json (git-ignored). No password, token, cookie value, e-mail, name or
PCE number is ever printed or written.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp
from yarl import URL

ROOT = Path(__file__).resolve().parent.parent
SENTINEL = ROOT / ".probe-refused"
REPORT = ROOT / "captures" / "login-probe.json"

START_URL = "https://monespace.grdf.fr/"
OKTA = "https://connexion.grdf.fr"
IDENTIFY_URL = OKTA + "/idp/idx/identify"
ANSWER_URL = OKTA + "/idp/idx/challenge/answer"
API = "https://monespace.grdf.fr/api"
IDX_HEADERS = {"Accept": "application/json; okta-version=1.0.0", "Content-Type": "application/json"}
API_HEADERS = {"Accept": "application/json", "Referer": "https://monespace.grdf.fr/entreprises/"}
# Same UA as PyGazpar for this first proof (known to pass); to be revisited for the integration.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
STATE_TOKEN = re.compile(r'"stateToken"\s*:\s*"([^"]+)"')

report: dict[str, Any] = {"started": datetime.now(UTC).isoformat(timespec="seconds"), "steps": []}
_secrets: list[str] = []


def scrub(text: str) -> str:
    for s in _secrets:
        if s:
            text = text.replace(s, "<redacted>")
    return text


def shape(obj: Any, depth: int = 0) -> Any:
    """Structure only: key names and value types, never values."""
    if isinstance(obj, dict):
        return "{…}" if depth >= 4 else {k: shape(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shape(obj[0], depth + 1), f"×{len(obj)}"] if obj else []
    return type(obj).__name__


def url_public(url: URL | str) -> str:
    u = URL(str(url))
    return f"{u.host}{u.path}" + ("?<" + ",".join(sorted(u.query.keys())) + ">" if u.query else "")


def idx_summary(body: Any) -> dict[str, Any]:
    """What matters in an Okta IDX answer, without stateHandle, user or tokens."""
    if not isinstance(body, dict):
        return {"json": False}
    out: dict[str, Any] = {"keys": sorted(body.keys())}
    rem = body.get("remediation", {}).get("value", [])
    out["remediations"] = [r.get("name") for r in rem if isinstance(r, dict)]
    for k in ("currentAuthenticator", "currentAuthenticatorEnrollment"):
        v = body.get(k, {}).get("value") if isinstance(body.get(k), dict) else None
        if isinstance(v, dict):
            out[k] = {"type": v.get("type"), "key": v.get("key")}
    auths = body.get("authenticators", {}).get("value", []) if isinstance(body.get("authenticators"), dict) else []
    out["authenticators"] = [a.get("type") for a in auths if isinstance(a, dict)]
    msgs = body.get("messages", {}).get("value", []) if isinstance(body.get("messages"), dict) else []
    out["messages"] = [{"class": m.get("class"), "key": (m.get("i18n") or {}).get("key"),
                        "message": scrub(str(m.get("message", "")))} for m in msgs if isinstance(m, dict)]
    for k in ("errorCode", "errorSummary", "expiresAt", "intent"):  # classic-style error bodies too
        if k in body:
            out[k] = scrub(str(body[k]))
    out["has_success"] = "success" in body
    return out


def step(name: str, **data: Any) -> None:
    report["steps"].append({"step": name, **data})
    print(f"[{name}] " + json.dumps(data, ensure_ascii=False))


def finish(outcome: str, code: int) -> int:
    report["outcome"] = outcome
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"\n=== {outcome} ===\nReport: {REPORT.relative_to(ROOT)}")
    return code


async def body_json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return json.loads(await resp.text())
    except ValueError:
        return None


async def main() -> int:
    if SENTINEL.exists():
        print(f"STOP — a previous attempt did not succeed ({SENTINEL.read_text().strip()}).\n"
              "Check the password in a browser, then delete .probe-refused yourself before trying again.")
        return 2

    print("GRDF headless login probe — ONE attempt, no retry.\n"
          "Before going on: the account must not be locked, and you must be sure of the password\n"
          "(ideally you have just logged in with it in a browser).")
    dry = "--dry-run" in sys.argv  # step 1 only: no identifier, no password
    username = "" if dry else input("GRDF username (e-mail): ").strip()  # noqa: ASYNC250 — nothing else runs yet
    password = "" if dry else getpass.getpass("GRDF password (not echoed): ")
    if dry:
        pass
    elif not username or not password:
        print("Empty input — nothing sent.")
        return 2
    _secrets.extend([password, username])

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, timeout=timeout) as s:
        # 1. Start: monespace redirects to the Okta authorize page, which embeds a stateToken.
        async with s.get(START_URL) as r:
            html = await r.text()
            step("start", status=r.status, hops=[url_public(h.url) for h in r.history], final=url_public(r.url))
        m = STATE_TOKEN.search(html)
        if not m:
            return finish("no stateToken in the login page — flow changed, nothing sent", 1)
        state = m.group(1).encode().decode("unicode_escape")  # \x2D → -
        step("state_token", length=len(state), jwt_like=state.count(".") >= 2, escapes_left="\\" in state)
        if dry:
            return finish("dry run — stopped before identify, nothing sent", 0)

        # 2. Identify (username only — not a password attempt).
        s.cookie_jar.update_cookies({"ln": username}, URL(OKTA + "/"))
        async with s.post(IDENTIFY_URL, json={"identifier": username, "stateHandle": state},
                          headers=IDX_HEADERS) as r:
            ident = await body_json(r)
            summary = idx_summary(ident)
            step("identify", status=r.status, **summary)
        if r.status != 200 or not isinstance(ident, dict):
            return finish("identify refused — password NOT sent", 1)
        handle = ident.get("stateHandle")
        wants_password = ("challenge-authenticator" in summary["remediations"]
                          and (summary.get("currentAuthenticatorEnrollment") or summary.get("currentAuthenticator")
                               or {}).get("type") == "password")
        if not handle or not wants_password:
            return finish("Okta is not asking for a password here (MFA / other step) — password NOT sent", 1)

        # 3. Password — the one and only attempt. Sentinel first; removed only on success.
        SENTINEL.write_text(f"attempt at {datetime.now(UTC).isoformat(timespec='seconds')}, outcome unknown\n")
        async with s.post(ANSWER_URL, json={"credentials": {"passcode": password}, "stateHandle": handle},
                          headers=IDX_HEADERS) as r:
            ans = await body_json(r)
            summary = idx_summary(ans)
            step("password", status=r.status, **summary)
        href = (ans.get("success") or {}).get("href") if isinstance(ans, dict) else None
        if r.status != 200 or not href:
            keys = ",".join(str(x["key"]) for x in summary.get("messages", [])) or summary.get("errorCode", "?")
            SENTINEL.write_text(f"refused at {datetime.now(UTC).isoformat(timespec='seconds')}: "
                                f"HTTP {r.status} {keys}\n")
            return finish(f"PASSWORD STEP NOT ACCEPTED (HTTP {r.status}, {keys}) — stopped, no retry", 1)

        # 4. Token redirect back to monespace (sets the session cookies).
        async with s.get(href) as r:
            await r.read()
            step("redirect", status=r.status, hops=[url_public(h.url) for h in r.history], final=url_public(r.url))
        SENTINEL.unlink(missing_ok=True)

        # 5. Session check + PCE list, ≥ 1 s apart, no redirects (a 30x means "not logged in").
        for name, path in (("whoami", "/e-connexion/users/whoami"), ("accreditations", "/e-accred/accreditations")):
            await asyncio.sleep(1.2)
            async with s.get(API + path, headers=API_HEADERS, allow_redirects=False) as r:
                j = await body_json(r)
                step(name, status=r.status, content_type=r.headers.get("Content-Type", ""),
                     json=j is not None, shape=shape(j) if j is not None else None)

        now = time.time()
        report["cookies"] = sorted(
            ({"name": c.key, "domain": c["domain"], "path": c["path"], "secure": bool(c["secure"]),
              "httponly": bool(c["httponly"]), "max_age": c["max-age"] or None, "expires": c["expires"] or None}
             for c in s.cookie_jar), key=lambda c: (c["domain"], c["name"]))
        print(f"cookies: {[(c['domain'], c['name']) for c in report['cookies']]} at t={int(now)}")

    checks = [st for st in report["steps"] if st["step"] in ("whoami", "accreditations")]
    ok = all(st.get("status") == 200 and st.get("json") for st in checks)
    return finish("LOGIN OK — session works" if ok else "login accepted but API check failed (see steps)",
                  0 if ok else 1)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
