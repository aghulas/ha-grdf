# GRDF customer API (monespace.grdf.fr) — as reverse-engineered for the MCP connector (Oct. 2026)

Unofficial, undocumented, may change. Source of truth for code: `~/dev/grdf-mcp/grdf_mcp/browser.py`.

## Base and allow-list (GET only)

Base: `https://monespace.grdf.fr`

| Path | Use |
|---|---|
| `/api/e-connexion/users/whoami` | session check (200 + JSON when logged in) |
| `/api/e-accred/accreditations` | PCE attached to the account (alias, PCE id, relève frequency, `teleReleve`) |
| `/api/e-conso/pce/consommation/informatives?dateDebut=YYYY-MM-DD&dateFin=YYYY-MM-DD&pceList[]=<PCE>` | daily data (one row per journée gazière) |
| `/api/e-conso/pce/consommation/publiees?dateDebut=…&dateFin=…&pceList[]=<PCE>` | monthly published readings (billing basis) |
| `/api/e-conso/pce/<PCE>/meteo?dateFinPeriode=YYYY-MM-DD&nbJours=N` | daily mean temperature for the site, `{ "YYYY-MM-DD": float }` |
| `/api/e-conso/pce/<PCE>/details` | meter details (allowed, not used) |

Request headers used: `Accept: application/json`, `Referer: https://monespace.grdf.fr/entreprises/`.
No redirects followed (`max_redirects=0`): a 30x means the session is gone.

## Response shape — daily / monthly rows

Response is keyed by PCE; each PCE has a list of `releves`. Fields used per row
(`normaliser_releve`):

| Field | Meaning |
|---|---|
| `journeeGaziere` | gas day (YYYY-MM-DD) — 06:00 that day → 06:00 next day |
| `dateDebutReleve`, `dateFinReleve` | ISO timestamps with offset |
| `energieConsomme` | kWh |
| `volumeBrutConsomme` | m³ |
| `indexDebut`, `indexFin` | meter index (m³) |
| `pcs` | conversion coefficient (kWh per m³) |
| `qualificationReleve` | "Mesuré", "Estimé"… |
| `natureReleve` | "Informative Journalier", "Publiée"… |

Notes: the daily endpoint selects on the **end** of each reading: `dateDebut=D1&dateFin=D2` returns the
gas days `D1 − 1` … `D2 − 1` (verified on the real service, 2026-11-18). To get the gas days `[start, end]`,
ask `dateFin = end + 1` and drop the extra day before `start`.

Seen on the same day: on 91-day ranges starting more than about a year back, the **last gas day was left
out** although a short range returns it; the three most recent 91-day ranges were complete. Cause unknown.
The client asks that day again on its own when it is missing from a long range. Today is never returned;
yesterday appears D+1 (morning). History available back to at least 2023 for a télérelevé meter.
Two years requested in 90-day ranges (daily + meteo, 19 calls 1 s apart) went through without any
HTML glitch on the first real run (2026-11-18, ≈ 27 s).

## Quirks

- Bursts get an **HTML page instead of JSON** (status 200): keep ≥ 1 s between calls, retry once after
  3 s, then `whoami` to tell "glitch" from "logged out".
- GRDF sometimes serves a full-page **"Erreur : 403 — problème technique"** (WAF / incident): back off.

## Authentication — headless login (proven 2026-11-18, supervised, one attempt)

GRDF uses **Okta Identity Engine** (IDX, sign-in widget 7.19.6) on `connexion.grdf.fr`. The MCP connector
does not log in programmatically (real Chrome + cookies); the integration does, with the flow below —
same as PyGazpar 1.3.1, reproduced with aiohttp by `scripts/login_probe.py` (one cookie jar for all steps).

| # | Request | Observed answer |
|---|---|---|
| 1 | `GET https://monespace.grdf.fr/` (redirects followed) | 200, lands on `connexion.grdf.fr/oauth2/<authServerId>/v1/authorize?response_type=code&scope=openid profile email&client_id=…&state=…&nonce=…&redirect_uri=https://monespace.grdf.fr/_codexch&by_pass_okta=1&capp=meg`. The HTML embeds `"stateToken":"…"` (JWT-like, ~4.4 kB, JS-escaped: `\x2D` → `-`). |
| 2 | `POST /idp/idx/identify` — JSON `{"identifier": <e-mail>, "stateHandle": <stateToken>}` | 200. `remediation.value[].name` = `challenge-authenticator`, `select-authenticator-authenticate`; `currentAuthenticatorEnrollment.value.type` = `password` (`key` = `okta_password`); `authenticators` = password only; new `stateHandle`; `expiresAt` ≈ now + 5 min. **Not a password attempt.** |
| 3 | `POST /idp/idx/challenge/answer` — JSON `{"credentials": {"passcode": <password>}, "stateHandle": <handle from 2>}` | 200 with `success.href`, no `remediation`, no `messages`. |
| 4 | `GET <success.href>` (redirects followed) | `connexion.grdf.fr/login/token/redirect?stateToken=…` → `monespace.grdf.fr/_codexch?code=…&state=…` → 200 on monespace. |
| 5 | `GET /api/e-connexion/users/whoami`, then `/api/e-accred/accreditations` | 200 `application/json` — the session works. |

IDX headers for 2 and 3: `Accept: application/json; okta-version=1.0.0`, `Content-Type: application/json`.
PyGazpar also sets a cookie `ln=<e-mail>` on `connexion.grdf.fr` before step 2 (kept; not proven necessary).
User-Agent used for the proof: PyGazpar's `Mozilla/5.0 (Windows NT 10.0; Win64; x64)` — to revisit (an
identifiable UA would make the integration recognisable in the Okta device list; untested).

Build the JSON bodies with a serializer (`json=`), not string formatting: PyGazpar's template breaks on a
password containing `"` or `\`, which would be sent wrong — and count as a refusal.

### Cookies after login (names only)

- `monespace.grdf.fr`: `auth_token`, `auth_nonce`, `auth_redir` (session cookies, Secure, HttpOnly — the
  session itself), plus `BIGipServer…` (load balancer) and `TS…` (WAF).
- `connexion.grdf.fr`: `idx`, `JSESSIONID` (session), `DT` (device token, 2 years), `luf_<n>` (30 days), `ln`.

None of the session cookies carries an expiry: the session lifetime is server-side and **not measured precisely**
(a session opened at 06:42 was found expired by 09:05 the same day: expect one login per daily fetch)
(the MCP connector's `sessions.jsonl` gives real-world durations). Persisting `DT` across logins would make
Okta see one known device instead of a new one each time — to decide when writing the client.

### Rules for the client (CLAUDE.md rule 1)

- Send the password **only if** step 2 answers 200 with `challenge-authenticator` and a current authenticator
  of type `password`. Anything else (MFA enrolment, other authenticator, non-200) → auth failure, **password
  not sent**.
- Step 3 without `success.href` — whatever the status or body — is a **refusal**: stop polling, Repairs
  issue, reauth flow. Never a second POST to `challenge/answer` without a user action.
- Steps 1, 2, 4, 5 carry no password; a network error there is retryable (next day). A network error *during*
  step 3 has an unknown outcome → treat as a refusal (do not resend).

### Not observed yet (only the success path was exercised — deliberately)

Refusal shapes are **not verified** against GRDF. Per Okta IDX documentation, expect on step 3 a 401/400 with
`messages.value[]` (`class: "ERROR"`, `i18n.key` such as `errors.E0000004` for invalid credentials); a locked
account may be reported the same way as a wrong password, or on step 2. The client must not depend on these
keys to decide (see rule above); log the `i18n.key` only, for diagnostics. Do not provoke a refusal on a real
account to find out — fixtures for these cases are synthetic.

## Response shape — `whoami` and `accreditations` (key names only)

- `whoami`: object with `id`, `first_name`, `last_name`, `email`, `profile`, `status`, `espace{…}`,
  `adresse{…}`, `raison_sociale`, phones, consent flags… — **all personal: redact entirely in diagnostics.**
- `accreditations`: list of objects; useful keys `pce`, `alias`, `teleReleve` (bool), `frequenceReleve`,
  `etat`, `role`, `dateMes`, `parcours`, `access[]`; personal: `nomTitulaire`, `codePostal`,
  `raisonSociale`, `denominationClient`, `adresseEmailClient`, `telephoneClient`, `idObject`,
  `idAccreditation`.
