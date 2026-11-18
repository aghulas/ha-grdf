# ha-grdf — Home Assistant integration for GRDF gas consumption (business and residential accounts)

Guide for Claude Code sessions on this repo. Goal: a **read-only**, community-grade HA integration that
imports the gas consumption published by GRDF (monespace.grdf.fr) into Home Assistant's long-term
statistics and Energy dashboard — daily values per *journée gazière*, monthly billing readings, site
temperature / degree days — **without ever locking the user's GRDF account**.

## What we are building

- **HA domain**: `grdf_conso` (distinct from the community `gazpar` integration to coexist).
- **Repo**: `aghulas/ha-grdf` (private at first, HACS Marketplace once hardened). MIT.
- Read `docs/requirements.md` (what to build, acceptance criteria) and `docs/api.md` (the GRDF API
  as reverse-engineered for the MCP connector) before writing code.

## Why a new integration (the incident that motivates it)

In Oct. 2026 the community integration `gazpar` (YAML, PyGazpar) kept polling GRDF every 15 min with
an **outdated password**: GRDF (Okta) locked the account every ~1 h 45 for three days. Existing tools
have no re-authentication flow and no brake on refusals. This integration exists to do the opposite.

## Non-negotiable rules

1. **Never retry a refused password.** First authentication refusal (401, Okta `E0000004`,
   `LOCKED_OUT`, MFA required…) → stop all polling for this entry, raise a Repairs issue and start the
   **reauth flow**. Only a user action restarts it. Unit-test this path first.
2. **Poll little**: one fetch per day after publication (GRDF publishes D+1, usually in the morning),
   with jitter; ≥ 1 s between HTTP calls; one retry on an HTML-instead-of-JSON answer, then back off.
3. **Statistics, not cumulative sensors**: import past days with their real date
   (`async_add_external_statistics` / `async_import_statistics`), idempotently (re-importing a day
   overwrites it). Backfill history on first setup (up to the configured depth).
4. **Read-only**: no write route of the GRDF API is ever called; keep an allow-list of paths, as the
   MCP connector does.
5. **Secrets & personal data**: password, session cookies/tokens, PCE numbers, e-mail — never in logs,
   diagnostics (redact), tests fixtures or the repo. Fixtures are synthetic. `git grep` before pushing.
   No data specific to the pilot site anywhere in this repo — it is meant to be public.

## Reuse (do not rewrite)

- `~/dev/grdf-mcp/grdf_mcp/` — the MCP connector (private repo): routes, allow-list, response shapes
  (`modeles.py` → `normaliser_releve`, `dju`), rate limiting and retry logic (`browser.py` → `Grdf.get`).
  The connector authenticates through a real Chrome session (cookies); **the integration cannot**
  (headless HA) and must log in with username/password — see `docs/api.md` §Authentication.
- `~/dev/grdf-mcp/skills/releve-gaz-grdf/SKILL.md` — data semantics (journée gazière, D+1, monthly vs
  daily, lockout investigation).
- PyGazpar (ssenart, MIT) — reference for the headless Okta login flow; read it, do not depend on it
  blindly (it has no lockout protection).
- `~/dev/ha-danfoss-ally` — layout, CI, config-flow / diagnostics / tests conventions to copy.

## Suggested first session

1. Prove the headless login against the real account **once**, by hand, with the user present
   (a refused password must stop everything — no loop). Record the flow in `docs/api.md`.
2. Write `api/` (client + models) with tests on synthetic fixtures, then the coordinator and the
   statistics import, then config flow + reauth + diagnostics.
3. CI green (hassfest, HACS action, ruff, mypy, pytest), then a manual test on the school's HA with
   the user.

## Definition of done (v0.1)

See `docs/requirements.md` §Acceptance. In short: config flow + reauth, daily kWh/m³ statistics in the
Energy dashboard with backfill, monthly readings, temperature/DJU, lockout-safe auth with a test proving
a refused password is tried exactly once, diagnostics redacted, CI green, README in English (+ French).
