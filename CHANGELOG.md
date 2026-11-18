# Changelog

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
