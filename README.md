# air-calib — rendre un capteur de pollution à 30 € aussi fiable qu'une station officielle

> **Question business.** Peut-on corriger un capteur de particules fines grand public (≈ 30 €,
> réseau citoyen [Sensor.Community](https://sensor.community)) pour qu'il approche la précision
> d'une station de mesure officielle (plusieurs dizaines de milliers d'euros) ? Et jusqu'à quelle
> distance d'une station de référence la correction reste-t-elle fiable ?

Clients visés : une collectivité qui veut un réseau de mesure dense à bas coût, un promoteur
immobilier, un assureur santé.

**Statut :** étape 2 (ingestion, couche bronze) — voir [`docs/PLAN.md`](docs/PLAN.md),
[`notebooks/01_sources_et_faisabilite.ipynb`](notebooks/01_sources_et_faisabilite.ipynb) et
[`notebooks/02_ingestion_bronze.ipynb`](notebooks/02_ingestion_bronze.ipynb).
Ce README sera complété au fil des étapes (résultats, figures, choix et limites).

## Un exemple de rigueur : l'horloge des stations, vérifiée et pas supposée

Comparer un capteur à une station n'a de sens que si les deux parlent de **la même heure**. Les métadonnées
officielles annoncent « UTC » pour les stations françaises et « UTC+01 » pour les allemandes. Plutôt que de les
croire, on l'a **mesuré** : pour des capteurs à moins de 500 m d'une station, on calcule la corrélation entre la série
horaire du capteur (horloge UTC, vérifiée contre l'API en direct) et celle de la station décalée de $k$ heures :

$$
k^\star = \arg\max_{k \in \{-3,\dots,3\}} \operatorname{corr}\big(\text{capteur}(h),\ \text{station}(h + k)\big)
$$

Résultat : $k^\star = +1$ h pour **7 paires sur 7, en Allemagne comme en France**. Les horodatages EEA de la
France ne sont donc pas en UTC malgré leurs métadonnées : la règle `heure_UTC = Start − 1 h` s'applique aux deux pays.
Sans ce contrôle, toutes les paires françaises auraient été comparées avec une heure de décalage, une erreur
silencieuse qui aurait dégradé le modèle sans jamais lever d'alerte. Ce contrôle sera généralisé à **toutes** les
paires dans la couche silver (une paire dont le meilleur décalage n'est pas nul après correction est exclue et
comptée). Détails : notebook 01, section 6, et [`docs/JOURNAL.md`](docs/JOURNAL.md).

## Le risque n°1, chiffré : l'humidité des sondes bon marché

L'humidité est la variable clé de la correction (les particules gonflent en air humide), mais les sondes des capteurs
citoyens tombent en panne en se mouillant. Sur 2024–2025, **53,5 % des mesures DHT22 sont bloquées à 100 %** et 6,7 %
à exactement 1 % ; **59 % des DHT22 sont « mortes » toute la période**, d'autant plus qu'elles sont vieilles. Quand une
sonde affiche 100 %, la station météo officielle voisine ne mesure 100 % que **2,1 %** du temps (médiane 85 %) : ce
n'est pas du brouillard, c'est une panne. Seules 38,9 % des heures-sondes sont récupérables ; pour les autres, le
pipeline utilise l'humidité officielle (DWD, Météo-France) de la station la plus proche, à 6,4 km en médiane.
Détails et figures : notebook 02, section 7.

## Données (100 % open data, coût 0 €)

| Source | Rôle | Format réel observé | Licence |
|---|---|---|---|
| Sensor.Community, archive `archive.sensor.community` | mesures brutes des capteurs citoyens (SDS011…) et de leurs sondes météo (BME280, DHT22…) | un CSV `;` gzip par capteur et par jour | ODbL / DbCL |
| Agence européenne de l'environnement (EEA), service de téléchargement Parquet | vérité terrain : PM2.5 horaire **vérifié** (E1a) des stations officielles | un Parquet par point de prélèvement, historique complet | données publiques EEA |
| Métadonnées EEA (`DataExtract.csv`) | position, type (trafic / fond), méthode de mesure des stations | CSV | données publiques EEA |
| Service météo allemand (DWD), `opendata.dwd.de` | humidité et température horaires officielles (secours des sondes saturées), Allemagne | un zip par station (texte `;`, UTC) | DWD Open Data (CC BY 4.0) |
| Météo-France, données climatologiques horaires (data.gouv.fr) | humidité et température horaires officielles, France | un CSV gzip par département et période | Licence Ouverte Etalab 2.0 |

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
make sample    # tout le pipeline sur 10 capteurs par pays et un mois (≈ 1 min)
make data      # volume complet (≈ 3 h de téléchargement + 13 min de conversion, reprenable ; 5,9 Go)
```

Commandes détaillées : `python -m aircalib discover` (étape 1 : inventaire, paires, contrôle d'horloge),
`download` (étape 2 : tous les fichiers bruts, avec manifeste et reprise), `bronze` (conversion en Parquet validé,
suppression des bruts convertis). Chaque commande accepte `--sample`.

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
