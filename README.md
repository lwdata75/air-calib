# air-calib — rendre un capteur de pollution à 30 € aussi fiable qu'une station officielle

> **Question business.** Peut-on corriger un capteur de particules fines grand public (≈ 30 €,
> réseau citoyen [Sensor.Community](https://sensor.community)) pour qu'il approche la précision
> d'une station de mesure officielle (plusieurs dizaines de milliers d'euros) ? Et jusqu'à quelle
> distance d'une station de référence la correction reste-t-elle fiable ?

Clients visés : une collectivité qui veut un réseau de mesure dense à bas coût, un promoteur
immobilier, un assureur santé.

**Statut :** étape 1 (faisabilité) terminée — voir [`docs/PLAN.md`](docs/PLAN.md) et
[`notebooks/01_sources_et_faisabilite.ipynb`](notebooks/01_sources_et_faisabilite.ipynb).
Ce README sera complété au fil des étapes (résultats, figures, choix et limites).

## Données (100 % open data, coût 0 €)

| Source | Rôle | Format réel observé | Licence |
|---|---|---|---|
| Sensor.Community, archive `archive.sensor.community` | mesures brutes des capteurs citoyens (SDS011…) et de leurs sondes météo (BME280, DHT22…) | un CSV `;` gzip par capteur et par jour | ODbL / DbCL |
| Agence européenne de l'environnement (EEA), service de téléchargement Parquet | vérité terrain : PM2.5 horaire **vérifié** (E1a) des stations officielles | un Parquet par point de prélèvement, historique complet | données publiques EEA |
| Métadonnées EEA (`DataExtract.csv`) | position, type (trafic / fond), méthode de mesure des stations | CSV | données publiques EEA |

## Pipeline (architecture médaillon)

```
Sources ──► raw/ (temporaire) ──► bronze/ ──► silver/ ──► gold/ ──► modèles + MLflow
```

- **bronze** : données brutes converties en Parquet zstd, contenu inchangé, + manifeste ;
- **silver** : nettoyé, typé, validé (Pandera), agrégé à l'heure (UTC, heure étiquetée par son début),
  particules et météo du même site jointes ;
- **gold** : une ligne par paire (capteur, station) et par heure, avec les features.

## Reproduire

```bash
make setup     # uv sync --frozen
make test      # tests unitaires hors-ligne
make lint      # ruff
python -m aircalib discover   # étape 1 : inventaire, paires candidates, contrôle d'horloge
```

Tout tourne en local ; les données sont écrites dans `data/` (jamais versionné).

## Choix et limites

Voir le journal [`docs/JOURNAL.md`](docs/JOURNAL.md) ; cette section en sera la synthèse à la fin du projet.

## Structure

```
configs/default.yaml   configuration unique (dates, zone, seuils, URLs)
src/aircalib/          tout le code (ingest/, transform/, cli.py…)
notebooks/             exploration et modèles (importent src/, aucune logique dupliquée)
tests/                 pytest, petits jeux de données piégeux
docs/                  PLAN.md (étapes) et JOURNAL.md (problèmes et contournements)
reports/               rapport d'entonnoir et figures
```
