# air-calib — rendre un capteur de pollution à 30 € aussi fiable qu'une station officielle

> **Question business.** Peut-on corriger un capteur de particules fines grand public (≈ 30 €,
> réseau citoyen [Sensor.Community](https://sensor.community)) pour qu'il approche la précision
> d'une station de mesure officielle (plusieurs dizaines de milliers d'euros) ? Et jusqu'à quelle
> distance d'une station de référence la correction reste-t-elle fiable ?

Clients visés : une collectivité qui veut un réseau de mesure dense à bas coût, un promoteur
immobilier, un assureur santé.

**Statut :** étape 7 (évaluation finale) — voir [`docs/PLAN.md`](docs/PLAN.md), les notebooks
[`01`](notebooks/01_sources_et_faisabilite.ipynb) à [`07`](notebooks/07_evaluation_finale.ipynb) et le rapport
d'entonnoir [`reports/funnel.md`](reports/funnel.md).

## La réponse, en trois phrases

**Oui : corrigé par un modèle appris sur d'autres stations, un capteur SDS011 en bon état passe les cibles de l'EPA
dans 80 % des cas en horaire et 85 % en journalier (contre 25 % et 39 % sans correction), et cela tient jusqu'à 3 km
d'une station (70 à 82 % selon la distance ; 81 à 92 % quand le capteur a au moins 3 voisins citoyens).** La vraie
limite n'est pas la distance mais la densité du réseau citoyen et le temps : sans voisins, moins d'un capteur sur deux
passe, et un modèle appris sur une année perd 15 points l'année suivante, il faut donc le réapprendre chaque année. Le
modèle allemand ne se transpose pas tel quel en France (23 % des capteurs sains en horaire, 35 % en journalier), où
61 % des heures n'ont pas de voisins : il y faut une calibration locale.

Les preuves : notebook [`07`](notebooks/07_evaluation_finale.ipynb) et la section « Hors des conditions d'apprentissage »
ci-dessous.

## Le point de départ : un capteur à 30 € sans correction

Les cibles de performance de l'agence américaine de l'environnement (EPA) pour les capteurs de PM2.5 : $R^2 \ge 0{,}70$,
pente $1 \pm 0{,}35$, ordonnée à l'origine entre −5 et +5 µg/m³, RMSE ≤ 7 µg/m³ (capteur régressé sur la référence).
**Sans aucune correction**, sur 2024–2025 :

| | horaire | journalier |
|---|---|---|
| Allemagne (1 229 / 1 192 paires) | **19,9 %** des paires passent les 4 cibles | **32,3 %** |
| France (136 / 123 paires) | **11,8 %** | **17,9 %** |

La cible la plus manquée est la pente : le capteur brut ne voit qu'environ 60 % de la référence, et ce rapport dépend
surtout de l'humidité (0,42 en air sec, 0,79 en air saturé). C'est le « avant » ; le modèle sera jugé sur la part de
paires qu'il fait passer de l'autre côté. Détails : notebook 04.

### Une surprise : le capteur sous-estime, même par temps humide

Les études sur les capteurs optiques à bas coût parlent surtout de **surestimation par temps humide** : les particules
gonflent d'eau et diffusent plus de lumière. Nos données montrent l'inverse au global : le SDS011 **sous-estime** la
référence, de plus de moitié en air sec (rapport 0,42), et l'humidité ne fait que **réduire** cette sous-estimation
(0,79 en air saturé). **Hypothèse la plus probable** : le capteur optique rate les plus fines particules, qui portent
une grande part de la masse de PM2.5, alors que l'instrument de référence sèche l'échantillon et le pèse (ou en
mesure la masse par une méthode étalonnée sur la pesée). La croissance par l'humidité existe bien, mais elle part
d'une lecture trop basse.

D'où le modèle physique retenu, **un gain et une hygroscopicité ajustés ensemble** sur l'entraînement :

$$
\mathrm{PM}_{\text{réf}} \approx a \cdot \frac{\mathrm{PM}_{\text{brut}}}{C(\mathrm{HR})}, \qquad
C = 1 + \frac{\kappa / \rho_p}{-1 + 1/a_w}, \qquad a_w = \frac{\mathrm{HR}}{100}
$$

avec $a = 2{,}39$ (le capteur voit 1/2,39 = 42 % de la référence en air sec) et $\kappa = 0{,}066$, un κ « effectif »
plus bas que l'hygroscopicité habituelle de l'aérosol, l'humidité officielle étant plafonnée à 95 %. La correction
κ-Köhler seule, qui divise la mesure, aurait aggravé l'écart. Détails : notebook 05.

## Après correction : 80 % des paires dans les cibles EPA, mais seulement 42 % quand le capteur est isolé

Le modèle retenu est un **LightGBM** (arbres de décision construits les uns après les autres, chacun corrigeant les
erreurs des précédents). Il prend 45 variables : mesure brute, humidité et température **officielles**, modèle
physique, heure et saison, dynamique des heures passées, capteur, contexte, et **médiane des capteurs citoyens
voisins**. Une contrainte garantit qu'une lecture brute plus haute ne fait jamais baisser la valeur corrigée.

Il est évalué par **validation croisée par station** : 5 plis, et une station de test n'est jamais vue à
l'entraînement. Le tableau porte sur l'Allemagne, à ≤ 1,5 km, en 2024–2025 :

| | RMSE horaire | % paires EPA, horaire | % paires EPA, journalier |
|---|---|---|---|
| Capteur brut (paires saines) | 11,0 µg/m³ | 24,8 % | 39,1 % |
| **Modèle, paires saines, toutes heures** | **3,84 µg/m³** | **79,7 %** | **85,3 %** |
| … heures avec ≥ 3 voisins citoyens (79 %) | 3,39 µg/m³ | 88,3 % | 92,0 % |
| … heures **sans** voisins (21 %) | 5,21 µg/m³ | 42,1 % | 58,6 % |
| Modèle, toutes paires (capteurs abîmés compris) | 4,38 µg/m³ | 74,7 % | 81,9 % |
| … avec voisins / sans voisins | 3,54 / 6,59 µg/m³ | 84,3 / 36,4 % | 90,2 / 49,6 % |

- **La performance vient des données et des variables, pas du réglage du modèle.** Le réglage automatique des
  hyperparamètres (Optuna) ne rapporte que ≈ 2 % de RMSE. Retirer les voisins citoyens coûte 21 % ; retirer la
  dynamique passée, 4 %. Passer d'une régression linéaire à LightGBM avec toutes les variables fait passer les paires
  EPA de 12 % à 77 %.
- **Limite principale** : une heure sur cinq n'a pas de voisins citoyens. Le RMSE monte alors à 5,2 µg/m³ (contre
  3,4 avec voisins) et moins de la moitié des paires passent les cibles EPA en horaire. La qualité de la correction
  dépend de la **densité du réseau**.
- **La pente EPA d'un modèle de régression suit son $R^2$** (ici 0,82 pour 0,76) : un modèle qui minimise l'erreur
  quadratique resserre ses prédictions vers la moyenne. Recalibrer la pente à 1 a été testé et rejeté, car le RMSE
  augmentait de 19 %.
- Le chiffre est **légèrement optimiste** : les hyperparamètres ont été choisis sur les mêmes plis. Avec les réglages
  par défaut, RMSE 3,91 et 77,3 % / 85,3 %. Le test sur 2025 et le transfert vers la France (étape 7) diront s'il
  tient.
- **Rejetés, avec chiffres** : modèle hybride (pas robuste aux capteurs abîmés), intervalle 10–90 % des quantiles (ne
  couvre que 62 %), sonde d'humidité du capteur (n'apporte rien). Détails : notebook 06 et journal.

## Hors des conditions d'apprentissage : temps, distance, France

% de paires saines passant les cibles EPA, horaire / journalier ; toujours sur des stations jamais vues à
l'entraînement, sauf (a).

| Test | Toutes heures | Avec voisins | Sans voisins |
|---|---|---|---|
| Référence (5 plis, Allemagne ≤ 1,5 km, 2024–2025) | 79,7 / 85,3 | 88,3 / 92,0 | 42,1 / 58,6 |
| (b) nouvelles stations, appris en 2024, testé en 2025 | 69,5 / 81,2 | 79,0 / 89,0 | 38,9 / 52,3 |
| (b0) témoin : nouvelles stations, appris et testé en 2025 | 84,3 / 88,9 | 92,0 / 93,2 | 55,8 / 69,8 |
| (a) mêmes stations, appris en 2024, testé en 2025 | 82,0 / 92,1 | 89,2 / 96,3 | 60,0 / 75,3 |
| Capteurs à < 0,5 km de la station | 81,7 / 88,3 | 87,4 / 90,9 | 50,0 / 66,7 |
| Capteurs à 2–3 km de la station | 70,1 / 80,1 | 81,2 / 91,3 | 49,2 / 60,0 |
| France, sans réajustement (brut : 15,8 / 24,2) | 22,8 / 35,2 | 50,0 / 52,9 | 12,7 / 24,2 |

- **Temps** : à volume d'apprentissage égal, un an d'écart coûte 15 points en horaire (b contre b0). Le modèle de
  2024 « comprime » les valeurs de 2025 : il surestime les mois propres et sous-estime les mois pollués. Il faut
  **réapprendre chaque année**.
- **Distance** : jusqu'à 3 km, elle compte peu. Le recul au-delà de 2 km vient surtout des heures sans voisins (38 %
  contre 15 % sous 0,5 km). Le PM2.5 est un polluant largement régional.
- **France** : le niveau moyen est juste, mais 61 % des heures n'ont pas de voisins, et les stations de référence
  (TEOM, jauges bêta) répondent autrement. Il faut une calibration locale.
- **Points faibles** : stations industrielles (0 % des 8 paires en horaire), TEOM en horaire (55 %, mais 91 % en
  journalier), brouillard (69 % au-delà de 95 % d'humidité).
- **Littérature** : $R^2$ 0,18 → 0,76 (0,81 avec voisins) sur des stations jamais vues, contre 0,55–0,71 → 0,76–0,80
  pour trois SDS011 testés sur le site même de la station à Oslo (Liu et al., 2019).
- **Intervalle** : la prédiction conforme ramène la couverture de l'intervalle 10–90 % de 62 % à 80 %, avec et sans
  voisins (largeur médiane 5,6 et 7,8 µg/m³).

## Un exemple de rigueur : l'horloge des stations, vérifiée et pas supposée, sur toutes les paires

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
silencieuse qui aurait dégradé le modèle sans jamais lever d'alerte.

Le contrôle est ensuite **automatique sur toutes les paires et toutes les stations** (couche silver). Une première
version, sur les *niveaux* de pollution, se trompait : les niveaux varient lentement, la corrélation est presque plate
entre 0 et ±1 h (0,920 contre 0,921 pour une excellente paire), et elle rejetait 171 paires sur 1 440 sans raison. Le
contrôle retenu corrèle les **variations d'une heure à l'autre**, dont le pic est net au vrai décalage :

$$
r(k) = \operatorname{corr}\big(\Delta\text{capteur}(h),\ \Delta\text{station}(h+k)\big),\qquad
z = \big(\operatorname{atanh} r(k^\star) - \operatorname{atanh} r(0)\big)\sqrt{\tfrac{n-3}{2}}
$$

Une paire (ou une station, toutes ses paires regroupées) est en décalage si son meilleur $k^\star \ne 0$ bat $k = 0$
avec $z \ge 3$ (test de Fisher).

**La contre-épreuve prouve qu'il fonctionne** : si l'on retire volontairement la correction `Start − 1 h`, il rejette
**1 085 paires vérifiables sur 1 142** et 207 stations sur 216 ; avec la correction, 29 paires et 7 stations
seulement. Parmi ces 7 stations, 5 mesurent avec un **TEOM**, une microbalance qui répond avec environ une demi-heure
de retard : ce n'est pas une erreur d'horloge, elles sont gardées et leur méthode de mesure devient une variable. Les
2 autres stations et 10 paires isolées sont exclues et comptées dans l'entonnoir. Détails : notebooks 01 (section 6)
et 03 (section 6), [`docs/JOURNAL.md`](docs/JOURNAL.md).

## Le risque n°1, chiffré : l'humidité des sondes bon marché

L'humidité est la variable clé de la correction (les particules gonflent en air humide), mais les sondes des capteurs
citoyens tombent en panne en se mouillant. Sur 2024–2025, **53,5 % des mesures DHT22 sont bloquées à 100 %** et 6,7 %
à exactement 1 % ; **59 % des DHT22 sont « mortes » toute la période**, d'autant plus qu'elles sont vieilles. Quand une
sonde affiche 100 %, la station météo officielle voisine ne mesure 100 % que **2,1 %** du temps (médiane 85 %) : ce
n'est pas du brouillard, c'est une panne. Seules 38,9 % des heures-sondes sont récupérables ; pour les autres, le
pipeline utilise l'humidité officielle (DWD, Météo-France) de la station la plus proche, à 6,4 km en médiane.
Détails et figures : notebook 02, section 7.

**D'où un choix de conception, et un argument métier :** l'humidité de la station météo officielle est l'entrée
**principale** du modèle (disponible pour 100 % des paires) ; celle de la sonde du capteur n'est qu'une variable
secondaire, présente quand elle est fiable (≈ 27 % des heures). **La correction n'exige donc pas que la sonde
d'humidité du capteur fonctionne** : la station météo publique la plus proche suffit.

## Un capteur en panne se repère sans station officielle

Un capteur qui ne suit plus l'air ne suit plus ses voisins. La corrélation des variations horaires d'un capteur avec
la médiane des capteurs citoyens à moins de 3 km, calculée **sans aucune donnée de station**, retrouve les capteurs
défectueux repérés par la station avec une **AUC de 0,90** (au seuil 0,1 : précision 84 %, rappel 37 %). Un réseau
de capteurs peut donc signaler lui-même ses capteurs suspects. Validé sur des stations jamais vues (notebook 06) :
65 % des signalements sont justes et 56 % des capteurs abîmés sont repérés. Le transfert vers la France reste faible
(précision 50 %, rappel 25 %, sur 50 paires) : c'est une **preuve de concept**, pas encore une fonction du produit.
Détails : notebook 05, section 6, et notebook 06, section 11.

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
- **silver** : un statut par mesure, agrégé à l'heure (UTC, heure étiquetée par son début), validé (Pandera) ;
  puis une ligne par segment de capteur et par heure avec la référence, la météo officielle et la sonde locale ;
- **gold** : une ligne par paire (capteur, station) et par heure, avec les features.

## Reproduire

```bash
make setup     # uv sync --frozen
make test      # tests unitaires hors-ligne
make lint      # ruff
make sample    # tout le pipeline sur 10 capteurs par pays et un mois (≈ 1 min)
make data      # volume complet (≈ 3 h de téléchargement, 13 min de bronze, 4 min de silver et gold ; ≈ 9,1 Go)
make train     # étape 6 : échelle de modèles, études annexes, modèle final (≈ 1 h 30, suivi MLflow)
make evaluate  # étape 7 : tests temps, distance, France, découpages, intervalle conforme (≈ 30 min)
make mlflow    # interface MLflow locale (sqlite:///mlflow.db)
```

Commandes détaillées : `python -m aircalib discover` (étape 1 : inventaire, paires, contrôle d'horloge),
`download` (étape 2 : tous les fichiers bruts, avec manifeste et reprise), `bronze` (conversion en Parquet validé,
suppression des bruts convertis), `silver` (étape 3 : un statut par mesure, agrégation horaire de chaque source),
`pair` (appariement par segment de position, jointures, contrôle d'horloge, rapport d'entonnoir). Chaque commande
accepte `--sample`. `features` (étape 5) construit la couche gold : les heures utilisables, 48 variables sans
référence ni futur (test anti-fuite), les plis de validation croisée par station et le modèle physique ajusté hors
pli. `train --study <étude>` (étape 6) entraîne et évalue un modèle ou une étude (`ladder`, `ablation`, `scale`,
`monotone`, `unverifiable`, `x`, `optuna`, `recalibration`, `final`, `attribution` ; étape 7 : `temporal`,
`distance`, `france`, `breakdowns`, `conformal`) et enregistre tout dans MLflow : paramètres, liste des variables,
empreinte de la table gold, métriques par pli, tables par tranche et prédictions hors pli.

Le nettoyage ne supprime aucune ligne : chaque mesure ou heure garde son statut (`ok` ou la première règle violée),
et [`reports/funnel.md`](reports/funnel.md) compte ce que chaque règle retire. Uniquement des règles physiques et de
cohérence, **aucun filtre statistique d'aberrations** : les pics par forte humidité sont ce que le modèle doit
apprendre à corriger ; une valeur répétée n'est « figée » que si c'est un code de panne ou si la station varie pendant
ce temps (vérifié : la règle ne retire pas l'air propre en priorité). Résultat : **13,8 millions d'heures-paires
utilisables**.

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
