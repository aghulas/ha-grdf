# GRDF consommations — Home Assistant integration

**Status: in development — the integration is written and tested against fakes (config flow with reauth, statistics import, sensors, diagnostics), but has not run on a real Home Assistant yet.** Unofficial integration, not affiliated with GRDF.

Imports the gas consumption published by GRDF for communicating meters (daily per *journée gazière*,
monthly billing readings, site temperature and degree days) into Home Assistant's long-term statistics
and Energy dashboard — read-only, and designed never to lock your GRDF account (a refused password is
tried once, then the integration asks you to re-authenticate).

See `docs/requirements.md` and `docs/api.md`. License: MIT.

---

*FR* — Intégration non officielle qui importe dans Home Assistant (statistiques, tableau de bord
Énergie) la consommation de gaz publiée par GRDF : par journée gazière, relevés mensuels, température
et degrés-jours. Lecture seule ; un mot de passe refusé n'est jamais réessayé.
