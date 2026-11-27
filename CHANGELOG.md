# Changelog

## 0.1.4 — 2026-11-27

Packaging only, no change in behaviour:

- `hacs.json` declares the country the integration is for (`FR`) and drops the undocumented
  `render_readme` key.
- The CI workflows run with a read-only token.
- README: status line no longer tied to a version, one-click link to add the repository in HACS,
  reason for the minimum Home Assistant version.

## 0.1.3 — 2026-11-19

- **Icon and logo**, shipped in the integration's `brand/` folder (Home Assistant 2026.3 and later
  show them; the Marketplace picks them up from the latest release). Original artwork — a flame over
  daily bars — not the logo of GRDF.
- The HACS validation now runs its brands check too.

## 0.1.2 — 2026-11-19

- **The reason of the last failed fetch is kept**, with its date, in the diagnostics and as attributes
  of the *connection problem* sensor (`last_error`, `last_error_at`). Meter numbers are masked;
  credentials never appear in these messages. Unexpected errors are recorded too, then logged with
  their traceback as before.

## 0.1.1 — 2026-11-19

Found on the pilot installation the day after 0.1.0:

- **History lost with the recorder database is imported again.** Home Assistant recreates an empty
  database when it finds its own corrupt, which wipes every long-term statistic. The integration now
  remembers the oldest gas day it imported and, at each fetch, checks that it is still in the
  statistics; if not, it imports the whole history again (daily and billing readings).
- **A fetch cut short by a restart is retried at startup** after 15 minutes, instead of waiting six
  hours or the next day. A fetch that ended — even in failure — still waits.
- Updating from 0.1.0 runs one full import, to record the oldest day imported.
- Diagnostics show the outcome of the last fetch.

## 0.1.0 — 2026-11-18

First release.

- Config flow with meter selection, re-authentication and options (English and French).
- Lockout-safe sign-in on GRDF's Okta: a password is sent at most once, and only when a password is what
  GRDF asks for; a refusal stops every request until the user re-authenticates, across restarts.
- Daily energy, volume, outdoor temperature and degree days imported as long-term statistics, idempotently,
  with backfill of the configured history (up to three years) and catch-up after downtime.
- Billing readings imported as their own statistics, as far back as GRDF serves them.
- Sensors for the last published values, a connection-problem sensor, a rate-limited refresh button.
- Redacted diagnostics.
- One fetch per day with jitter; no retry of a failed fetch before the next day.
- The integration identifies itself to GRDF with its own User-Agent.

Found on the first real runs and handled:

- GRDF selects daily readings on their end date (`dateFin` must be the day after the last wanted gas day);
- on long ranges reaching far back, GRDF can leave out the last gas day: it is asked again on its own.
