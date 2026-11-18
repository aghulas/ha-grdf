# GRDF consommations — Home Assistant integration

Unofficial integration, not affiliated with GRDF. *[Version française plus bas.](#français)*

Imports the gas consumption published by GRDF (monespace.grdf.fr) for communicating meters into Home
Assistant's long-term statistics and Energy dashboard: daily values per *journée gazière*, billing
readings, site temperature and degree days. Read-only, for business and residential accounts.

**Status: early.** It has been running on one real Home Assistant since November 2026 (three years of
daily history imported and checked against GRDF). Not released yet.

## Why another GRDF integration

It is built around one rule: **never lock your GRDF account.** GRDF locks an account after several
refused passwords, and a tool that keeps polling with an outdated password locks it again and again.
Here:

- a password is sent **once**. If GRDF refuses it — or asks for an extra verification step — the
  integration stops contacting GRDF, raises a repair issue and asks you to re-authenticate. Nothing is
  sent again until you do, not even after a restart;
- GRDF is contacted **once a day**, after the data is published, with at least one second between
  requests. A failed fetch waits for the next day.

This behaviour is covered by automated tests against a local fake of GRDF; it has deliberately not been
provoked on a real account.

## Installation

Requires Home Assistant 2025.11 or later.

1. Copy `custom_components/grdf_conso` into the `custom_components` folder of your Home Assistant
   configuration (HACS support will come with the first release).
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **GRDF consommations**.
4. Enter the e-mail and password of your monespace.grdf.fr account, then pick the meters (PCE) to import
   if the account has several.

Check first that you can sign in on monespace.grdf.fr with that password. On the first setup the
history is imported (about one request per second: two years take under a minute).

## Options

| Option | Default | Notes |
|---|---|---|
| History to import | 730 days | Up to 1095. GRDF keeps about three years of daily data. Raising it imports the older days at the next fetch. |
| Daily fetch hour | 10 | Local time; a random delay of up to 30 minutes is added. GRDF publishes a day on the next morning. |
| Base temperature for degree days | 18 °C | Changing it does not recompute the degree days already imported. |
| Fetch the monthly billing readings | on | |

## What you get

**Statistics** (per meter, external statistics `grdf_conso:<pce>_…`):

| Statistic | Content |
|---|---|
| `energy` (kWh), `volume` (m³) | one value per gas day, at 06:00 Europe/Paris |
| `temperature` (°C), `dju` | daily mean outdoor temperature given by GRDF, and degree days |
| `billed_energy` (kWh), `billed_volume` (m³) | one value per published billing period, at the start of the period |

**Energy dashboard**: Settings → Dashboards → Energy → Gas → add **one** of `energy` or `volume` (Home
Assistant does not mix the two units). Do not add the `billed` statistics next to it: the gas would be
counted twice. Today and yesterday are usually empty, since GRDF publishes with a one-day delay.

**Entities** (one device per meter): last day energy and volume, last billed period energy (period,
indexes and conversion factor as attributes), outdoor temperature, last published day, data quality,
last billing reading, a *connection problem* sensor and a *refresh* button (at most one fetch every
five minutes).

## If GRDF refuses the sign-in

The integration stops, and a repair issue appears. Then:

1. sign in on monespace.grdf.fr in a browser — unlock the account or reset the password if needed;
2. look for any other tool still using an old password (another integration, a script) and stop it,
   otherwise the account will be locked again;
3. re-authenticate the integration from Settings → Devices & services. The password is tried once.

## Privacy

Your password is stored by Home Assistant like any other integration credential. The meter number (PCE)
is used as an identifier of the statistics and entities. Downloaded diagnostics contain neither the
password, the e-mail, the PCE nor the meter's name.

## Development

See `CLAUDE.md`, `docs/requirements.md` and `docs/api.md` (the GRDF portal as observed: unofficial,
undocumented, subject to change). `scripts/login_probe.py` is a supervised one-shot check of the sign-in
flow (`--dry-run` stops before any credential is sent).

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check . && mypy custom_components/grdf_conso && pytest
```

License: MIT.

---

## Français

Intégration non officielle, sans lien avec GRDF.

Elle importe dans Home Assistant (statistiques à long terme et tableau de bord Énergie) la consommation
de gaz publiée par GRDF (monespace.grdf.fr) pour les compteurs communicants : valeurs par journée
gazière, relevés de facturation, température du site et degrés-jours. Lecture seule, pour les comptes
professionnels et particuliers.

**État : jeune.** Elle tourne sur un vrai Home Assistant depuis novembre 2026 (trois ans d'historique
journalier importés et recoupés avec GRDF). Pas encore de version publiée.

### Pourquoi une autre intégration GRDF

Elle est construite autour d'une règle : **ne jamais verrouiller votre compte GRDF.** GRDF verrouille un
compte après plusieurs mots de passe refusés, et un outil qui continue d'interroger GRDF avec un ancien
mot de passe le verrouille encore et encore. Ici :

- un mot de passe est envoyé **une seule fois**. Si GRDF le refuse, ou demande une étape de vérification
  supplémentaire, l'intégration cesse de contacter GRDF, crée une alerte dans Réparations et vous
  demande de vous ré-authentifier. Rien n'est renvoyé avant cela, même après un redémarrage ;
- GRDF est contacté **une fois par jour**, après la publication des données, avec au moins une seconde
  entre deux requêtes. Une collecte en échec attend le lendemain.

Ce comportement est couvert par des tests automatiques sur un faux serveur GRDF ; il n'a volontairement
pas été provoqué sur un vrai compte.

### Installation

Home Assistant 2025.11 ou plus récent.

1. Copier `custom_components/grdf_conso` dans le dossier `custom_components` de la configuration de
   Home Assistant (HACS viendra avec la première version publiée).
2. Redémarrer Home Assistant.
3. Paramètres → Appareils et services → Ajouter une intégration → **GRDF consommations**.
4. Saisir l'e-mail et le mot de passe du compte monespace.grdf.fr, puis choisir les compteurs (PCE) à
   importer si le compte en a plusieurs.

Vérifiez d'abord que ce mot de passe fonctionne sur monespace.grdf.fr. À la première installation,
l'historique est importé (environ une requête par seconde : deux ans prennent moins d'une minute).

### Options

| Option | Défaut | Remarques |
|---|---|---|
| Historique à importer | 730 jours | Jusqu'à 1095. GRDF conserve environ trois ans de données journalières. L'augmenter importe les jours plus anciens à la collecte suivante. |
| Heure de la collecte quotidienne | 10 | Heure locale ; un délai aléatoire de 30 minutes au plus s'ajoute. GRDF publie une journée le lendemain matin. |
| Température de base des degrés-jours | 18 °C | La changer ne recalcule pas les degrés-jours déjà importés. |
| Récupérer les relevés mensuels de facturation | oui | |

### Ce que vous obtenez

**Statistiques** (par compteur, statistiques externes `grdf_conso:<pce>_…`) :

| Statistique | Contenu |
|---|---|
| `energy` (kWh), `volume` (m³) | une valeur par journée gazière, à 06:00 heure de Paris |
| `temperature` (°C), `dju` | température extérieure moyenne du jour fournie par GRDF, et degrés-jours |
| `billed_energy` (kWh), `billed_volume` (m³) | une valeur par période de facturation publiée, au début de la période |

**Tableau de bord Énergie** : Paramètres → Tableaux de bord → Énergie → Gaz → ajouter **une seule** des
séries `energy` ou `volume` (Home Assistant ne mélange pas les deux unités). N'ajoutez pas les séries
`billed` à côté : le gaz serait compté deux fois. Aujourd'hui et hier sont en général vides, GRDF
publiant avec un jour de décalage.

**Entités** (un appareil par compteur) : énergie et volume du dernier jour, énergie de la dernière
période facturée (période, index et coefficient de conversion en attributs), température extérieure,
dernier jour publié, qualité de la donnée, dernier relevé de facturation, un capteur *problème de
connexion* et un bouton *actualiser* (une collecte toutes les cinq minutes au plus).

### Si GRDF refuse la connexion

L'intégration s'arrête et une alerte apparaît dans Réparations. Ensuite :

1. connectez-vous sur monespace.grdf.fr dans un navigateur ; déverrouillez le compte ou changez le mot
   de passe si besoin ;
2. cherchez tout autre outil qui utiliserait encore un ancien mot de passe (une autre intégration, un
   script) et arrêtez-le, sinon le compte sera de nouveau verrouillé ;
3. ré-authentifiez l'intégration depuis Paramètres → Appareils et services. Le mot de passe est essayé
   une seule fois.

### Vie privée

Votre mot de passe est conservé par Home Assistant comme pour toute intégration. Le numéro de compteur
(PCE) sert d'identifiant aux statistiques et aux entités. Les diagnostics téléchargés ne contiennent ni
le mot de passe, ni l'e-mail, ni le PCE, ni le nom du compteur.
