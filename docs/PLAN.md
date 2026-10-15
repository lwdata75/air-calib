# Plan par étapes et statut

Règle : une étape à la fois ; à la fin de chaque étape, résumé et feu vert avant la suivante.

| # | Étape | Statut | Livrables principaux |
|---|---|---|---|
| 0 | Socle du projet | ✅ terminé | arborescence, `pyproject.toml` (uv), `Makefile`, `configs/default.yaml`, ruff/pytest, `.gitignore`, CLAUDE.md, PLAN, JOURNAL |
| 1 | Exploration des sources et faisabilité (GO / NO-GO) | ✅ terminé (GO) | `ingest/{http,sensor_community,eea,discovery}.py`, `transform/{pairing,hourly}.py`, `notebooks/01_sources_et_faisabilite.ipynb`, `python -m aircalib discover` |
| 2 | Ingestion à l'échelle (bronze) | ✅ terminé | `ingest/{plan,download,bronze,manifest,report,dwd,meteo_france}.py`, `schemas/bronze.py`, `transform/{physics,humidity,positions}.py`, `notebooks/02_ingestion_bronze.ipynb`, `reports/ingestion.md` |
| 3 | Nettoyage (silver) | ✅ terminé | `transform/{particles,runs,reference,weather,clock,funnel}.py`, `silver/{sources,pairs,rules,report}.py`, `schemas/silver.py`, `notebooks/03_nettoyage_silver.ipynb`, `reports/funnel.md`, `python -m aircalib silver` puis `pair` |
| 4 | Exploration des heures-paires | ✅ terminé | `evaluation/metrics.py` (régression, cibles EPA, profils d'erreur), `notebooks/04_exploration_paires.ipynb` |
| 5 | Feature engineering (couche gold) | ✅ terminé | `gold/build.py`, `transform/{physics,features,neighbours}.py`, `evaluation/{folds,metrics}.py`, `schemas/gold.py`, `notebooks/05_features_gold.ipynb`, `python -m aircalib features` |
| 6 | Modélisation + MLflow | ✅ terminé | `models/{data,ladder,cv,tracking,studies,results,runtime}.py`, `evaluation/{scoring,health}.py`, `notebooks/06_modeles.ipynb`, `python -m aircalib train --study <étude>`, `make train` |
| 7 | Évaluation finale, transfert, conclusions | ✅ terminé — en attente de feu vert | `models/generalisation.py`, `evaluation/conformal.py`, `notebooks/07_evaluation_finale.ipynb`, `make evaluate` |
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

## Détail de l'étape 6 (fait)
1. MLflow local (`sqlite:///mlflow.db`) : un run par modèle ou variante, avec paramètres, variables, empreinte gold,
   métriques par pli et regroupées (horaire et journalier, µg/m³, paires saines et toutes paires, avec et sans
   voisins), prédictions hors pli et figures.
2. Échelle de modèles sur les 5 plis par station, volume complet : brut → linéaire en log → physique → LightGBM →
   hybride → quantiles. LightGBM : RMSE 3,95, 76,7 % / 85,6 % de paires EPA (paires saines).
3. Études annexes sur 20 % des paires (graine 42) :
   - ablation par famille (le voisinage décide : +21 % de RMSE sans lui) ;
   - log contre µg/m³ (µg retenu) ;
   - contrainte monotone (gardée) ;
   - invérifiables à l'entraînement (léger gain, non retenu) ;
   - X = 0,1 / 0,3 / 0,5 (négligeable) ;
   - recalibration de la pente (rejetée) ;
   - Optuna (15 essais, −2 %).
4. Modèle retenu, réentraîné sur le volume complet : LightGBM en µg/m³, 45 variables (sans la sonde locale),
   monotone, réglages Optuna. RMSE 3,84 µg/m³, 79,7 % / 85,3 % de paires EPA (saines) ; avec voisins 3,39 et
   88,3 % / 92,0 % ; sans voisins 5,21 et 42,1 % / 58,6 %.
5. Score de santé : seuil choisi sur 4 plis et mesuré sur le 5ᵉ (précision 65 %, rappel 56 %) ; transfert vers la
   France faible (50 % / 25 %).

## Détail de l'étape 7 (fait)
0. Vérification préalable (`attribution`) : le recul « toutes paires » vient de la cible en µg/m³, qui apporte sur les
   paires saines ; le modèle retenu est gardé (règle fixée au feu vert, justification au journal).
1. Test temporel : (a) mêmes stations 2024 → 2025, (b) nouvelles stations 2024 → 2025, (b0) témoin 2025 → 2025 ;
   coût de l'année d'écart : −15 points de % EPA horaire ; croisement avec l'âge du capteur.
2. Test distance par station (5 tranches jusqu'à 3 km) : 82 % → 70 % ; avec voisins, 81 à 92 % à toutes distances.
3. Transfert France sans réajustement : 22,8 % / 35,2 % (paires saines), contre 15,8 % / 24,2 % brutes.
4. Découpages : type de station, méthode, saison, humidité.
5. Comparaison à Liu et al. (2019, Oslo).
6. Bonus : intervalle conforme, couverture 62 % → 80 % (calibration par voisinage).
7. Score de santé présenté comme preuve de concept.

## Engagements pour l'étape 8 (finition)
- README final (synthèse des choix et limites à partir du journal) ; `make sample` depuis un clone propre ; lint et
  tests au vert ; `requirements.txt`.
- Pistes non faites, à mentionner comme suites : calibration locale française, réapprentissage annuel, modèle dédié
  aux heures sans voisins, réseau citoyen complet comme voisinage, intervalle conforme autour du modèle retenu.

## Engagements pour l'étape 7 (issus de l'étape 6, tenus)
- Test 2024 → 2025 (dérive) ; test « distance » par station (bandes ≤ 1,5 km et 1,5–3 km) ; transfert vers la France
  sans recalibrer. Ces trois tests sont **hors sélection** des hyperparamètres.
- Garder le découpage « avec / sans voisins » dans **tous** les tableaux finaux, y compris le % de paires EPA.
- À mesurer : un modèle dédié aux heures sans voisins (gain attendu ≈ 0,1 µg/m³) ; un intervalle calibré par
  station (conforme), l'intervalle quantile ne couvrant que 62 % au lieu de 80 % ; l'entraînement avec les paires
  invérifiables (proposition) ; la cible log contre µg/m³ sur toutes paires (le µg/m³ y perd sur le volume complet).
- Score de santé en France : comprendre la faiblesse du transfert (voisinage, effectif).

## Décisions du feu vert de l'étape 5 (14/10/2026)
- **Paires invérifiables exclues de l'entraînement**, évaluées à part ; chaque chiffre clé donné pour les « paires
  saines » et « toutes paires ».
- **Couche gold** : uniquement les heures utilisables et les variables retenues.
- **Modèle physique à deux paramètres** (gain $a$ et κ, ajustés ensemble sur l'entraînement, hors pli) ; humidité
  officielle plafonnée (`physics.rh_cap_pct`).
- **Échelle log préparée** (plancher `features.log_floor_ugm3`) ; correction de Duan dans `evaluation/metrics.py` ;
  décision log / linéaire à l'étape 6 ; métriques EPA en µg/m³.

## Détail de l'étape 5 (fait)
1. Plis par station (GroupKFold glouton, 5 plis de 47–48 stations allemandes ; la France hors plis).
2. Modèle physique : $a = 2{,}39$, $\kappa = 0{,}066$ (stables d'un pli à l'autre), estimation hors pli.
3. 48 variables en 9 familles (capteur brut, météo officielle, sonde locale, physique, temps, dynamique passée,
   capteur, contexte, voisinage citoyen) ; test anti-fuite.
4. Couche gold : 13,82 M heures-paires, 1,1 Go ; score de santé par les voisins (AUC 0,90).

## Décisions du feu vert de l'étape 4 (14/10/2026)
- Contrôle d'horloge sur les variations horaires (z de Fisher, contre-épreuve) adopté et mis en avant dans le README.
- **Stations TEOM gardées** (`instrument_lag`) ; `method_class` (méthode de mesure EEA) pour toutes les stations, comme
  variable et axe de lecture ; DEBY099 et DEMV007 restent exclues.
- **Paires invérifiables gardées** avec leur drapeau ; comparées dans le notebook 04 ; test avec et sans à l'étape 6.
- **Disque** : statuts en `pl.Enum`, bruts EEA supprimés (10,28 → 9,43 Go).
- **Valeurs figées** : seulement code de panne (0/0) ou station qui varie ≥ 5 µg/m³ (vérification air propre faite).

## Détail de l'étape 4 (fait)
1. Le « avant » : cibles EPA sans correction, horaire et journalier, par pays, bande, type de station et méthode.
2. Erreur brute selon l'humidité officielle, la saison, le type de station, la méthode, la bande, l'âge ; dérive
   annuelle par paire.
3. Forme de la relation (rapport stable, erreur absolue croissante → échelle log proposée).
4. Variabilité entre paires ; paires invérifiables contre vérifiées.

Résultat : **19,9 % des paires allemandes passent les cibles EPA en horaire et 32,3 % en journalier sans correction**
(France 11,8 % et 17,9 %) ; l'humidité fait passer le rapport capteur / référence de 0,42 à 0,79.

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
- **Étape 6** :
  - validation croisée **groupée par station (GroupKFold, `cv_group`)** : une station n'est jamais à la fois en
    entraînement et en test (144 stations ont des heures utilisables dans les deux bandes) ;
  - **test « distance » par station** : dans chaque pli, une station de test et **toutes** ses paires (≤ 1,5 km et
    1,5–3 km) sont exclues de l'entraînement ; on entraîne sur les paires ≤ 1,5 km des **autres** stations, puis on
    mesure l'erreur sur les paires de la station de test, **bande par bande** ;
  - test de sensibilité à X (0,1 / 0,3 / 0,5), test avec et sans les paires invérifiables ;
  - échelle log contre µg/m³ (erreur multiplicative, notebook 04), sur les mêmes plis ;
  - erreur sur 2025 d'un modèle appris sur 2024 (dérive) ;
  - résultats par type de station et par méthode de mesure ;
  - **chaque chiffre clé deux fois** : « paires saines » et « toutes paires » (les invérifiables, exclues de
    l'entraînement, sont repérées grâce à la station) ;
  - modèle physique hors pli comme variable et comme modèle de référence simple ; facteur de Duan calculé sur les
    résidus d'entraînement de chaque pli et vérifié hors pli ;
  - la pente en log (0,47) montre une régression vers la moyenne : comparer un modèle à pente libre au modèle
    physique à pente 1.
