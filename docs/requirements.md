# Requirements — ha-grdf (`grdf_conso`)

## Users and use cases

- A business or residential GRDF customer with a communicating (télérelevé) gas meter wants their gas
  consumption in HA's **Energy dashboard** and in long-term statistics, day by day.
- An energy manager wants to compare gas use with outdoor temperature / degree days (DJU) to spot a
  heating left on during holidays or a drift in efficiency.

## Configuration

- **Config flow** (UI only, no YAML): username (e-mail), password → login test → list the PCE meters
  attached to the account (`/api/e-accred/accreditations`) → the user picks one or several.
- **Options**: history depth to backfill (default 2 years, max what GRDF serves), fetch hour (default
  after 10:00 local), DJU base temperature (default 18 °C), include monthly readings (yes).
- **Reauth flow** when credentials are refused or expire; never a silent retry (see CLAUDE.md rule 1).
- Several config entries (several accounts) supported; several PCE per entry.

## Data and entities

| Data | Source | In HA |
|---|---|---|
| Daily energy (kWh) per *journée gazière* (06:00 → 06:00) | `consommation/informatives` | external statistic `grdf_conso:<pce>_energy` (sum, kWh), hourly-aligned at the day start, backfilled |
| Daily volume (m³) | same | external statistic `grdf_conso:<pce>_volume` (sum, m³) |
| Monthly billing readings (kWh, m³, index) | `consommation/publiees` | sensor "last billed period" (+ attributes), optional statistic |
| Daily mean outdoor temperature | `pce/<pce>/meteo` | external statistic `grdf_conso:<pce>_temperature` (mean, °C) |
| Degree days (DJU = max(0, base − T)) | computed | external statistic `grdf_conso:<pce>_dju` (sum) |
| Last published day, last reading date, data quality ("Mesuré"…) | | diagnostic sensors |
| Connection status | | binary sensor (problem) + Repairs issue on auth failure |

- Units: kWh and m³ must be selectable in the Energy dashboard gas section.
- Statistics import is **idempotent** and covers late corrections (re-import the last 7 days daily).

## Behaviour

- One fetch per day after the configured hour, with random jitter (0–30 min); manual "refresh" button.
- ≥ 1 s between HTTP calls; on HTML instead of JSON: wait 3 s, retry once, then check `whoami` before
  concluding to an expired session (same logic as the MCP connector).
- Session expired → re-login once with stored credentials; refused → stop + Repairs + reauth.
- HTTP 403 "problème technique" pages from GRDF → back off (next day), no retry storm.

## Non-functional

- No blocking I/O in the event loop (aiohttp via HA's session).
- Diagnostics: redact password, cookies, tokens, PCE, e-mail, names, addresses.
- Translations: `en`, `fr`.
- Tests: pytest-homeassistant-custom-component; coverage ≥ 80 % on `api/` and the coordinator.

## Acceptance (v0.1)

1. Config flow adds an account and one PCE; history is backfilled; the Energy dashboard shows gas.
2. A test proves that a refused password triggers **exactly one** login attempt, a Repairs issue and
   the reauth flow, and no further HTTP call until reauth.
3. Daily statistics match the MCP connector's `grdf_consommations` output for the same days (manual
   check on the real account, with the user).
4. DJU statistic matches `grdf_meteo` for the same period.
5. CI green: hassfest, HACS action, ruff, mypy, pytest.
