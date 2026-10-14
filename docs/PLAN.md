# Plan par étapes et statut

Règle : une étape à la fois ; à la fin de chaque étape, résumé et feu vert avant la suivante.

| # | Étape | Statut | Livrables principaux |
|---|---|---|---|
| 0 | Socle du projet | ✅ terminé | arborescence, `pyproject.toml` (uv), `Makefile`, `configs/default.yaml`, ruff/pytest, `.gitignore`, CLAUDE.md, PLAN, JOURNAL |
| 1 | Exploration des sources et faisabilité (GO / NO-GO) | ✅ terminé (GO) | `ingest/{http,sensor_community,eea,discovery}.py`, `transform/{pairing,hourly}.py`, `notebooks/01_sources_et_faisabilite.ipynb`, `python -m aircalib discover` |
| 2 | Ingestion à l'échelle (bronze) | ✅ terminé | `ingest/{plan,download,bronze,manifest,report,dwd,meteo_france}.py`, `schemas/bronze.py`, `transform/{physics,humidity,positions}.py`, `notebooks/02_ingestion_bronze.ipynb`, `reports/ingestion.md` |
| 3 | Nettoyage (silver) | ✅ terminé — en attente de feu vert | `transform/{particles,runs,reference,weather,clock,funnel}.py`, `silver/{sources,pairs,rules,report}.py`, `schemas/silver.py`, `notebooks/03_nettoyage_silver.ipynb`, `reports/funnel.md`, `python -m aircalib silver` puis `pair` |
| 4 | Exploration des heures-paires (gold 1) | ⏳ à faire | paires × heures (faites en silver, étape 3) : effet de l'humidité officielle, dérive, type de station, paires invérifiables, notebook 04 |
| 5 | Feature engineering (gold 2) | ⏳ à faire | familles de features documentées, κ-Köhler ajusté, notebook 05 |
| 6 | Modélisation + MLflow | ⏳ à faire | échelle de modèles, GroupKFold par station, test 2025, ablation, Optuna, notebooks 10–11 |
| 7 | Évaluation finale, transfert, conclusions | ⏳ à faire | cibles EPA, transfert DE → FR, erreur vs distance, notebook 12 |
| 8 | Finition | ⏳ à faire | README final, `make sample` depuis un clone propre, requirements, lint/tests au vert |

## Décisions prises (feu vert du 14/10/2026)
- **Appariement : 1 capteur = 1 station**, la plus proche ayant des heures vérifiées pendant l'activité du capteur ;
  distance conservée.
- **Rayons** : téléchargement jusqu'à **3 km** (`pairing.radius_download_km`), entraînement sur les paires
  **≤ 1,5 km** (`pairing.radius_train_km`, bande `near`) ; la bande 1,5–3 km (`far`) est un **jeu de test
  « distance »**.
- **SDS011 seul** (`pairing.sensor_types`) ; PMS/SPS exclus (limite notée au journal).
- **Période** : 2024-01-01 → 2025-12-31.

## Décisions du feu vert de l'étape 3 (14/10/2026)
- **X = 0,3** validé (`humidity.dead_month_share`, reste dans la config) ; **test de sensibilité à l'étape 6** : modèle
  entraîné avec X = 0,1 / 0,3 / 0,5, conclusions comparées.
- **Humidité officielle = entrée principale** (100 % des paires) ; humidité locale = variable secondaire, seulement si
  fiable. Argument métier : la correction n'exige pas de sonde d'humidité fonctionnelle. L'étape 5 compare les deux.
- **Station météo** : la plus proche du **capteur**, départage par la complétude ; distance gardée.
- **Particules** : règles physiques et de cohérence seulement (bornes 0–999,9, saturation, PM2.5 > PM10, valeurs
  figées, doublons), **aucun filtre statistique** ; complétude ≥ 75 % de la cadence propre de chaque capteur ; moyenne,
  médiane, écart-type et nombre de mesures gardés.
- **Capteurs déplacés** : segments de position, appariement par segment.
- **Un statut par mesure** (`quality_flag`), entonnoir calculé à partir des statuts.
- **Découpage** : une station n'est jamais à la fois en entraînement et en test, quelle que soit la distance
  (`cv_group`).

## Détail de l'étape 1 (fait)
1. Échantillon réel Sensor.Community (SDS011, BME280, DHT22) : colonnes, types, anomalies → journal.
2. EEA : métadonnées, codes polluants, colonnes du Parquet, drapeaux, disponibilité 2024–2025, horloge.
3. Découverte des capteurs actifs sur 2024–2025 (un listing d'archive par mois + lecture d'en-tête par Range HTTP).
4. Comptage des paires par rayon (0,5 / 1 / 1,5 / 2 / 3 km), Allemagne et France (corrigé à l'étape 2 : une station
   par capteur).
5. Notebook 01 et recommandation GO / NO-GO chiffrée.

## Détail de l'étape 2 (fait)
1. Plan : paires finales (`bronze/reference/pairs.parquet`), sondes d'humidité du même site, unités capteur × mois
   sur la fenêtre d'activité.
2. Téléchargement asynchrone poli et reprenable (manifestes JSONL) : Sensor.Community, EEA PM2.5 + PM10, DWD,
   Météo-France ; dates d'enregistrement des capteurs (âge des sondes).
3. Conversion bronze : Parquet zstd, contrats Pandera stricts, compteurs d'audit, suppression des bruts convertis.
4. Rapport `reports/ingestion.md`, notebook 02 (volumes, échecs, durée, disque, saturation de l'humidité).
5. `physics.py` : κ-Köhler jamais appliqué à une humidité saturée ni au-dessus de `physics.rh_max_pct`.
6. Qualité de l'humidité (`transform/humidity.py`) : statut de chaque mesure (manquante / hors bornes / figée basse /
   saturée / utilisable), mois-sondes « morts » au-delà de X = 0,3 d'heures en défaut, croisement avec l'âge, preuve
   contre la météo officielle.

Résultat (volume complet) : 45 337 unités, 1,21 million de fichiers, 667,7 millions de lignes, **0 échec** ; 3 h 04 de
téléchargement, 12 min 35 s de conversion ; pic de disque ≈ 11 Go, final 5,9 Go ; **38,9 % des heures-sondes
d'humidité récupérables**.

## Détail de l'étape 3 (fait)
1. `silver` : statut de chaque mesure SDS011 (8 règles ordonnées), segments de position, heures avec complétude selon
   la cadence ; heures EEA en UTC avec statut ; météo officielle avec statut ; heures-sondes avec fiabilité.
2. `pair` : appariement par segment, choix de la station météo, jointure heure-paire, contrôle d'horloge par paire et
   par station (variations horaires, z de Fisher, contre-épreuve), statut de chaque heure-paire, `reports/funnel.md`.
3. Contrats Pandera silver (statuts connus, clés uniques, cohérence des heures `ok`), 89 tests.

Résultat : **13,28 millions d'heures-paires utilisables** (84,7 %) ; DE near 5,34 M, DE far 6,86 M, FR near 0,40 M,
FR far 0,68 M ; 64 paires exclues par l'horloge (dont 5 stations TEOM au temps de réponse retardé).

## Engagements pour les étapes suivantes
- **Étape 3 (silver)** — fait, voir ci-dessus ; engagements d'origine :
  - humidité **saturée** (≥ `humidity.saturation_pct`) ou **figée** = **manquante**, jamais une vraie valeur :
    `reading_status` (vide/`NaN`, hors bornes, < `humidity.floor_pct`, saturée), mois morts
    (`humidity.dead_month_share` = 0,3) entièrement écartés, heures en défaut écartées dans les mois sains, valeurs
    constantes sur plusieurs heures à détecter ; feature binaire « humidité locale fiable » ; humidité officielle
    (DWD / Météo-France) en secours, en prenant la station la plus proche **du capteur** parmi les rangs 1 et 2 ;
    distance capteur ↔ station météo gardée comme variable ;
  - **contrôle d'horloge automatique sur toutes les paires** (corrélation décalée après correction
    `Start − 1 h`) : une paire dont le meilleur décalage n'est pas 0 est signalée, exclue et comptée dans
    `reports/funnel.md` ;
  - capteurs déplacés pendant la période : mesures découpées par position (distance recalculée) ;
  - sentinelles `-999` (EEA, DWD) et `NaN` → manquant ; codes qualité Météo-France douteux (2) écartés ;
  - `sensor_type` normalisé en minuscules (le contenu est en majuscules).
- **Étape 5** : l'humidité « fiable » des sondes lit trop sec (−13 points BME280) : comparer humidité locale, officielle
  et locale corrigée par l'officielle ; âge de la sonde comme variable candidate.
- **Étapes 4 → 7** : `station_type` et `station_area` conservés dans gold ; **toutes les évaluations rapportées par
  type de station** (trafic / fond urbain / périurbain / rural).
- **Étape 4** : explorer les heures-paires (effet de l'humidité officielle, dérive, type de station) ; décider du sort
  des 298 paires « invérifiables » et des stations TEOM exclues (variable « méthode de mesure » ?).
- **Étape 6** : validation croisée **groupée par station (GroupKFold, `cv_group`) sur toutes les distances** : une
  station n'est jamais à la fois en entraînement et en test (139 stations ont des heures utilisables dans les deux
  bandes) ; **test de sensibilité à X** (0,1 / 0,3 / 0,5).
