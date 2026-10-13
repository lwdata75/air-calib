# Plan par étapes et statut

Règle : une étape à la fois ; à la fin de chaque étape, résumé et feu vert avant la suivante.

| # | Étape | Statut | Livrables principaux |
|---|---|---|---|
| 0 | Socle du projet | ✅ terminé | arborescence, `pyproject.toml` (uv), `Makefile`, `configs/default.yaml`, ruff/pytest, `.gitignore`, CLAUDE.md, PLAN, JOURNAL |
| 1 | Exploration des sources et faisabilité (GO / NO-GO) | ✅ terminé — en attente de feu vert | `ingest/{http,sensor_community,eea,discovery}.py`, `transform/{pairing,hourly}.py`, `notebooks/01_sources_et_faisabilite.ipynb`, `python -m aircalib discover` |
| 2 | Ingestion à l'échelle (bronze) | ⏳ à faire | téléchargeur async reprenable + manifeste, CSV → Parquet, EEA E1a PM2.5/PM10 DE+FR, notebook 02 |
| 3 | Nettoyage (silver) | ⏳ à faire | règles qualité testées, agrégation horaire (≥ 75 %), jointure particules ↔ météo, contrats Pandera, entonnoir, notebook 03 |
| 4 | Appariement et exploration (gold 1) | ⏳ à faire | paires capteur ↔ station, effet de l'humidité, dérive, notebook 04 |
| 5 | Feature engineering (gold 2) | ⏳ à faire | familles de features documentées, κ-Köhler, notebook 05 |
| 6 | Modélisation + MLflow | ⏳ à faire | échelle de modèles, GroupKFold par station, test 2025, ablation, Optuna, notebooks 10–11 |
| 7 | Évaluation finale, transfert, conclusions | ⏳ à faire | cibles EPA, transfert DE → FR, erreur vs distance, notebook 12 |
| 8 | Finition | ⏳ à faire | README final, `make sample` depuis un clone propre, requirements, lint/tests au vert |

## Détail de l'étape 1 (fait)
1. Échantillon réel Sensor.Community (SDS011, BME280, DHT22) : colonnes, types, anomalies → journal.
2. EEA : métadonnées, codes polluants, colonnes du Parquet, drapeaux, disponibilité 2024–2025, horloge.
3. Découverte des capteurs actifs sur 2024–2025 (un listing d'archive par mois + lecture d'en-tête par Range HTTP).
4. Comptage des paires par rayon (0,5 / 1 / 1,5 / 2 / 3 km), Allemagne et France.
5. Notebook 01 et recommandation GO / NO-GO chiffrée.

### Résultat de l'étape 1 : GO
- DE à 1,5 km : 568 capteurs, 185 stations, ≈ 6,2 M heures communes estimées, 84 % avec sonde d'humidité.
- FR à 1,5 km : 62 capteurs, 47 stations (test de transfert).
- Horloge EEA vérifiée : `heure_UTC = Start − 1 h` (DE et FR).
- Disque estimé : ≈ 2,5 Go bronze capteurs + ≈ 0,8 Go EEA.

## Décisions en attente (à valider avec l'utilisateur avant l'étape 2)
- Rayon : **1,5 km recommandé** (alternative : 3 km = 1 269 capteurs DE, ≈ + 3 h de téléchargement, ≈ + 3 Go).
- Périmètre capteur : **SDS011 uniquement** pour l'entraînement (98 % des paires DE).
- Période : 2024-01-01 → 2025-12-31 (E1a vérifié disponible sur toute la période).
