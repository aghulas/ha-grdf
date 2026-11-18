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

Notes: the daily endpoint also returns the day before `dateDebut` → filter. Today is never returned;
yesterday appears D+1 (morning). History available back to at least 2023 for a télérelevé meter.

## Quirks

- Bursts get an **HTML page instead of JSON** (status 200): keep ≥ 1 s between calls, retry once after
  3 s, then `whoami` to tell "glitch" from "logged out".
- GRDF sometimes serves a full-page **"Erreur : 403 — problème technique"** (WAF / incident): back off.

## Authentication (to do in this project)

- The MCP connector does **not** log in programmatically: the user logs in in a real Chrome profile,
  the connector reuses the cookies. HA cannot do that.
- GRDF uses **Okta** (`connexion.grdf.fr`, mails from `noreply@connexion.grdf.fr`). PyGazpar logs in
  headlessly (Okta authn API → session → `monespace.grdf.fr`). Document the exact flow here after the
  first supervised test, including the error codes for "wrong password", "locked out", "MFA".
- Okta locks the account after several refused passwords; unlock is by e-mail link. **One attempt per
  user action**, never a loop (CLAUDE.md rule 1).
