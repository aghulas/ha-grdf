# GRDF consommations — Home Assistant integration

**Status: early — running on one real Home Assistant since 2026-11-18 (two years of history imported and checked against GRDF day by day); not released yet.** Unofficial integration, not affiliated with GRDF.

Imports the gas consumption published by GRDF for communicating meters (daily per *journée gazière*,
monthly billing readings, site temperature and degree days) into Home Assistant's long-term statistics
and Energy dashboard — read-only, and designed never to lock your GRDF account (a refused password is
tried once, then the integration asks you to re-authenticate).

## Statistics

Per meter, as external statistics (`grdf_conso:<pce>_…`):

| Statistic | Content |
|---|---|
| `energy` (kWh), `volume` (m³) | one value per gas day, at 06:00 Europe/Paris — pick **one** of them as the gas source of the Energy dashboard |
| `temperature` (°C), `dju` | daily mean outdoor temperature given by GRDF, and degree days |
| `billed_energy` (kWh), `billed_volume` (m³) | one value per published billing period, at the start of the period — do not add them to the Energy dashboard next to the daily ones (the gas would be counted twice) |

History: the daily series is imported for the configured depth (default 2 years; GRDF keeps about 3), the
billing readings for as long as GRDF serves them (about 5 years). Raising the depth in the options imports
the older days at the next fetch.

See `docs/requirements.md` and `docs/api.md`. License: MIT.

---

*FR* — Intégration non officielle qui importe dans Home Assistant (statistiques, tableau de bord
Énergie) la consommation de gaz publiée par GRDF : par journée gazière, relevés mensuels, température
et degrés-jours. Lecture seule ; un mot de passe refusé n'est jamais réessayé.
