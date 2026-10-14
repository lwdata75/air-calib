# CLAUDE.md — contexte persistant du projet air-calib

## Le projet
Projet data de groupe (Mastère Data & IA). **Question business** : peut-on corriger un capteur
de particules fines grand public (~30 €, Sensor.Community, surtout SDS011) pour qu'il approche
une station officielle (EEA), et jusqu'à quelle distance d'une station la correction reste fiable ?
- Zone principale : **Allemagne** (entraînement + validation croisée). **France** : test de transfert.
- Période : 2024-01-01 → 2025-12-31 (configurable).
- **Appariement : 1 capteur = 1 station** (la plus proche avec des heures vérifiées), SDS011 seul.
  Téléchargement ≤ 3 km ; entraînement sur la bande `near` (≤ 1,5 km) ; bande `far` (1,5–3 km) = test « distance ».
  GroupKFold par station sur **toutes** les distances. Évaluations toujours rapportées **par type de station**.
- Cible : PM2.5 horaire de la station EEA (E1a vérifié). Entrées : PM brut + **humidité officielle (DWD / Météo-France),
  entrée principale** + humidité de la sonde du même site seulement si fiable (variable secondaire) + contexte.
- Humidité saturée (≥ 99,9 %) ou figée (< 5 %, hors bornes, `NaN`) = **manquante** (jamais une vraie valeur) ;
  mois-sonde « mort » si > 30 % d'heures en défaut (`humidity.dead_month_share`) ; κ-Köhler jamais appliqué au-delà
  de `physics.rh_max_pct`.

## Contraintes non négociables
- Coût 0 € : open data + open source, tout en local (MacBook M5 Pro, 48 Go).
- Données réelles et sales, plusieurs sources ; pas de Kaggle.
- Livrable = repo GitHub public `lwdata75/air-calib` : `.py` (récupération, nettoyage), notebooks
  `.ipynb` (exploration, modèles), `requirements.txt` généré depuis `uv.lock`, README.
- **La note dépend surtout de la qualité du code de nettoyage/transformation.**
- Disque : 5–10 Go au final, 20 Go max en pic. Les CSV bruts sont supprimés après conversion.

## Règles de code
- Python 3.12, `uv`. Polars lazy et/ou DuckDB ; jamais de boucle pandas ligne par ligne.
- Contrats Pandera (Polars) par couche. Config unique `configs/default.yaml` (Pydantic Settings),
  aucune valeur magique dans le code.
- CLI Typer : `python -m aircalib <discover|download|bronze|silver|pair|features|all> [--sample]`.
- Téléchargements : httpx async, ≤ 8 requêtes simultanées, backoff exponentiel, reprise, manifeste,
  User-Agent explicite, HTTP/1.1 (l'archive casse HTTP/2).
- Fonctions pures + tests pytest avec petits jeux piégeux écrits à la main. `ruff`, type hints, docstrings courtes.
- Rapport d'entonnoir `reports/funnel.md` régénéré à chaque exécution.
- `data/`, `mlruns/`, `mlflow.db` gitignorés : jamais de données dans le repo.

## Règles pédagogiques
- Toute la logique dans `src/aircalib/` ; les notebooks importent, exécutent, montrent, expliquent.
- Chaque bloc de code est précédé d'un Markdown : **Ce qu'on fait / Pourquoi / Ce qu'on observe**.
- Terme technique défini à sa première apparition ; formules en LaTeX affiché `$$ ... $$`.
- Tout problème/surprise → `docs/JOURNAL.md` (Constat / Pourquoi / Contournement / Impact).
- Ne jamais cacher une limite. Notebooks/README/journal en **français** ; code/docstrings en **anglais**.

## Méthode de travail
- Une étape à la fois (voir `docs/PLAN.md`), résumé + feu vert de l'utilisateur avant la suivante.
- Vérifier sur données réelles avant d'affirmer ; si la réalité contredit le plan, la réalité gagne
  (on le note au journal et on prévient).
- Itérer en `--sample` avant le volume complet. Commit propre (anglais) à la fin de chaque étape.

- Nettoyage : **aucune ligne supprimée**, un statut par mesure/heure (`quality_flag` / `status`), entonnoir calculé
  à partir des statuts ; règles physiques et de cohérence seulement, **jamais de filtre statistique** sur les particules.
- Une paire = `(capteur, segment de position) → station` (`pair_id`) ; `cv_group` = code de la station (GroupKFold).

## Architecture (médaillon)
`sources → data/raw (temporaire) → data/bronze (Parquet zstd, contenu inchangé + manifeste)
→ data/silver (nettoyé, horaire UTC, particules+météo jointes) → data/gold (paires × heures + features)
→ modèles + MLflow (sqlite:///mlflow.db)`. Tables de l'étape 1 (inventaires, stations) : `data/meta/` ;
tables de référence du plan (paires, sondes, stations météo) : `data/bronze/reference/`.
`--sample` = mêmes commandes sur `data/sample/` (10 capteurs par pays, un mois : `sample.month`).
Manifestes JSONL : `raw/<source>/_manifest.jsonl` (téléchargé) et `bronze/<source>/_manifest.jsonl` (converti).
Silver : `silver/sensor_community/{pm/<id>.parquet, pm_hourly, segments, humidity_hourly}`, `silver/eea/hourly`,
`silver/weather/hourly`, `silver/pairs/{segment_pairs, weather_choice, clock_check*, pair_hours}` ; règles pures dans
`transform/`, étapes dans `silver/`, contrats dans `schemas/silver.py`.

## Faits vérifiés sur les vraies données (détails dans docs/JOURNAL.md)
- Archive Sensor.Community : années passées en `/<AAAA>/<AAAA-MM-JJ>/…csv.gz` (gzip !), année en
  cours en `/<AAAA-MM-JJ>/`. Un listing de jour ≈ 5 Mo et ≈ 65 s. Horodatages UTC (vérifié via l'API live).
- EEA : un Parquet par point de prélèvement avec tout l'historique ; E1a contient déjà 2024 et 2025.
  PM2.5 = 6001, PM10 = 5. `Value` en Decimal ; manquants = -999 / Validity -1. Horloge à décalage fixe
  (pas de trou au changement d'heure) — `heure_UTC = Start − 1 h` en DE **et** FR (corrélation décalée, notebook 01).
- DWD : zip par station (`historical/`, contrôlé, jusqu'au 31/12/2025), UTC, `-999` = manquant, liste des stations
  à largeur fixe en Latin-1. Météo-France : un CSV gzip par département et période (URL via l'API data.gouv.fr),
  beaucoup de stations sans humidité, Corse = `20`.
- Les stations officielles aussi mesurent 100 % d'humidité (brouillard), mais rarement (1,7 % DWD, 2,8 % Météo-France) ;
  quand une sonde sature, la station voisine ne l'est que 2,1 % du temps : panne de sonde. DHT22 : 53,5 % de mesures
  saturées + 6,7 % figées à 1,0 % ; 59 % des DHT22 mortes toute la période ; la mort augmente avec l'âge.
- Pièges vus en vrai : `NaN` (texte `nan`) ≠ vide et Polars le classe au-dessus de tout (`NaN >= 99.9` est vrai) ;
  virgule décimale chez certains firmwares ; `sensor_type` en MAJUSCULES dans le contenu ; capteurs déplacés (un
  capteur était à Hanoï avant Magdebourg). Horloges DWD et Météo-France vérifiées UTC (pas de saut hiver/été).
- SDS011 : saturation = PM2.5 999,9 avec PM10 1 999,9 ; code d'erreur −1/−1 ; cadences de 60 à 300 s (médiane 146 s).
- Contrôle d'horloge sur les *variations* horaires (pas les niveaux), z de Fisher, par paire et par station ; les
  stations TEOM répondent avec ≈ ½ h de retard : `instrument_lag`, **gardées** ; `method_class` sur chaque paire.
- Valeur SDS011 « figée » seulement si code de panne (0/0) ou station qui varie ≥ 5 µg/m³ pendant le plateau.
- Bruts EEA supprimés après bronze (`eea.keep_raw: false`) ; la découverte relit sa disponibilité en cache.
- Sans correction : 19,9 % des paires DE passent les cibles EPA en horaire, 32,3 % en journalier ; erreur
  multiplicative (rapport ≈ 0,5–0,6) dépendant surtout de l'humidité ; paires « invérifiables » ≈ capteurs défectueux.
- Modèle physique : PM_réf ≈ a·PM_brut/C(HR) (gain + κ, HR officielle plafonnée à 95 %), a = 2,39, κ = 0,066, ajusté
  hors pli sur la zone d'entraînement (DE, ≤ 1,5 km, paires saines) ; κ-Köhler seul aggraverait (le capteur sous-estime).
- Gold : `data/gold/{pair_hours,pairs,physics_fits}.parquet` ; 48 variables (`schemas/gold.FEATURES`), plis par station
  (`fold`, France = null) ; log avec plancher 1 µg/m³ ; Duan seulement pour un modèle à pente libre (pente log ≈ 0,47).
- Chaque chiffre clé de l'étape 6 : « paires saines » ET « toutes paires », et « avec / sans voisins ».
- Étape 6 : `python -m aircalib train --study <étude>` (MLflow, `mlruns/` gitignoré) ; macOS : `ensure_openmp()` relance
  le processus avec le `libomp` de scikit-learn. Modèle retenu : LightGBM en µg/m³, 45 variables (sans sonde locale),
  monotone, réglages Optuna (essai 14 sur 15) ; RMSE 3,84, 79,7 % / 85,3 % de paires EPA (saines) ; sans voisins
  (21 % des heures) RMSE 5,21 et 42 % : limite principale. Optuna ≈ 2 % ; voisinage ≈ 21 %. Pente EPA ≈ R².

## Convention horaire du projet
Une heure est étiquetée par son **début en UTC** : `13:00` = `[13:00, 14:00[` UTC.
