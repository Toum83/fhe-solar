# FHE Solar — intégration Home Assistant

Remonte dans Home Assistant la production solaire et la consommation mesurées par
un boîtier **FHE Drive&Elec** (pinces ampèremétriques), via le portail
[fhesmart.fhe-france.com](https://fhesmart.fhe-france.com). FHE n'expose pas d'API
publique : cette intégration reproduit les appels de l'application web.

## Entités

| Entité | Unité | Classe | Usage |
|---|---|---|---|
| `sensor.<appareil>_puissance_produite` | W | power / measurement | temps réel |
| `sensor.<appareil>_puissance_consommee` | W | power / measurement | temps réel |
| `sensor.<appareil>_production_du_jour` | kWh | energy / total_increasing | **Dashboard Énergie → Production solaire** |
| `sensor.<appareil>_consommation_du_jour` | kWh | energy / total_increasing | |
| `sensor.<appareil>_autoconsommation_du_jour` | kWh | energy / total_increasing | |
| `sensor.<appareil>_soutirage_reseau_du_jour` | kWh | energy / total_increasing | **Dashboard Énergie → Réseau, consommation** |
| `sensor.<appareil>_injection_reseau_du_jour` | kWh | energy / total_increasing | **Dashboard Énergie → Réseau, retour** |
| `sensor.<appareil>_taux_d_autoconsommation` | % | | part de la production consommée sur place |
| `sensor.<appareil>_taux_d_autoproduction` | % | | part de la consommation couverte par le solaire |
| prévisions J / J+1 | — | diagnostic | valeur brute FHE (unité non confirmée) |

Les compteurs « du jour » sont remis à zéro par FHE à minuit ; en
`total_increasing` le tableau de bord Énergie gère cette remise à zéro nativement.

## Service `fhe_solar.get_weekly_report`

Renvoie (réponse d'action) le bilan d'une période — par défaut la dernière
semaine complète du lundi au dimanche — calculé à partir des statistiques
journalières FHE : production, consommation, autoconsommation, soutirage,
injection, taux, économies (€) et détail par jour. Les prix du kWh se règlent
dans les options de l'intégration ou en paramètres du service.

```yaml
action: fhe_solar.get_weekly_report
data:
  start_date: "2026-09-14"   # optionnel
  end_date: "2026-09-20"     # optionnel
response_variable: report
```

## Installation

### Via HACS (dépôt personnalisé)

Le dépôt doit contenir `custom_components/fhe_solar` **à sa racine** : pousser
le contenu de ce dossier `ha-integration/` dans un dépôt GitHub dédié
(ex. `thomashuchet/fhe-solar`), puis dans HACS → ⋮ → *Dépôts personnalisés* →
ajouter l'URL, catégorie *Intégration* → installer → redémarrer HA.

### Manuelle

Copier `custom_components/fhe_solar` dans `/config/custom_components/` (Samba,
SSH ou l'app *File editor*), puis redémarrer HA.

### Configuration

Paramètres → Appareils et services → *Ajouter une intégration* → **FHE Solar** →
saisir le code utilisateur (ou l'email) et le mot de passe du portail FHE Smart.

Options (⚙️ sur l'entrée) : intervalle de rafraîchissement (5 min par défaut),
prix du kWh acheté et prix de rachat du surplus.

### Dashboard Énergie

Paramètres → Tableaux de bord → Énergie :

- **Réseau électrique** : consommation = `…soutirage_reseau_du_jour`,
  retour = `…injection_reseau_du_jour`
- **Panneaux solaires** : `…production_du_jour`

## Développement

```bash
python3 -m venv .venv && .venv/bin/pip install aiohttp pytest
.venv/bin/python -m pytest tests            # parsing sur réponses réelles
FHE_USERNAME=... FHE_PASSWORD=... .venv/bin/python tests/live_check.py   # test en direct
```

`custom_components/fhe_solar/api.py` ne dépend pas de Home Assistant et peut être
réutilisé dans n'importe quel script Python.

## API FHE reconstituée

| Requête | Réponse |
|---|---|
| `GET /` puis `POST /checkCookies` | ouvre la session PHP (`PHPSESSID`) |
| `POST /login` — `username` ou `email`, `password`, `remember` | `{"success": true, "redirect_url": "/dashboard"}` ou `{"success": false, "message": "…"}` |
| `GET /dashboard/data/all_widgets` | `dataNetwork` (kWh du jour : `prod`, `conso`, `autoconso`, `reseau`, `surprod`), `drive_elec_lines[].serie` (W, pas de 7 s), `previsionData` |
| `GET /stats/data/json?date=<unix>&pas=hours\|days\|months` | `series.conso_prod_home[]` (Wh par heure / jour / mois), `autoconsoprod_data` (% `autoconso` / `autoprod`), `global.drive_elecs.*.financial` (€) |

Session expirée → `302` vers `/` ; le client se reconnecte automatiquement.
