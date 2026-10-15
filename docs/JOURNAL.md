# Journal de bord : problèmes, surprises et contournements

Chaque entrée suit le format : **Constat** (avec un chiffre) · **Pourquoi** · **Contournement** · **Impact**.
Ce journal alimente la section « Choix et limites » du README.

---

## Étape 1 — Sources et faisabilité

### [2026-10-13] Les archives des années passées sont compressées en gzip
- **Constat** : dans `/2024/2024-03-15/`, les 18 905 fichiers capteurs se terminent par `.csv.gz`, et non `.csv` comme prévu dans le cahier des charges. Un fichier SDS011 d'une journée pèse ≈ 5,7 Ko compressé (574 lignes).
- **Pourquoi** : l'association recompresse les années closes pour économiser de l'espace ; seule l'année en cours reste en dossiers `AAAA-MM-JJ/` à la racine.
- **Contournement** : le code construit l'URL selon l'année (`sensor_community.day_url`) et décompresse en mémoire (`zlib`, `wbits=31`).
- **Impact** : volume téléchargé ≈ 4 à 5 fois plus faible que prévu ; le dossier `raw/` peut rester quasi vide (conversion en flux).

### [2026-10-13] Lister un jour d'archive coûte ≈ 65 secondes, et le serveur coupe sous la charge
- **Constat** : la page d'index d'un jour fait ≈ 4,9 Mo (≈ 18 900 fichiers) et met ≈ 65 s à arriver. Avec 8 listings en parallèle, 3 jours sur 24 ont échoué (`Server disconnected without sending a response`), même après 5 réessais. L'archive casse aussi le HTTP/2 (`Error in the HTTP2 framing layer`).
- **Pourquoi** : serveur Apache associatif qui génère l'index à la volée ; probablement une limite de connexions longues.
- **Contournement** : HTTP/1.1 forcé ; les listings ont leur propre limite (2 simultanés, `discovery.listing_concurrency`) ; un échec n'interrompt plus le lot, et chaque listing réussi est mis en cache (`data/meta/sc_listings/`) pour que la relance reprenne là où on s'était arrêté.
- **Impact** : la découverte complète prend ≈ 2–3 min de listings + ≈ 6 min de lectures d'en-tête (≈ 23 400 requêtes Range, ≈ 70 requêtes/s, 0 échec), une seule fois.

### [2026-10-13] Comment lister les capteurs actifs *sur la période* (et pas seulement aujourd'hui)
- **Constat** : l'API live ne renvoie que les capteurs des 5 dernières minutes (17 680 capteurs le 13/10/2026). Un capteur mort en 2025 y est invisible. Lister les 731 jours de l'archive prendrait ≈ 13 h.
- **Pourquoi** : aucun index « capteur → période d'activité → position » n'est publié. Les fichiers `csv_per_month/` existent mais pèsent ≈ 4 Go par mois rien que pour le SDS011 (≈ 100 Go sur 2 ans).
- **Contournement** (méthode retenue, comparée à 2 alternatives) :
  1. un listing par mois (le 15) → 24 listings → table capteur × mois d'activité ;
  2. pour chaque capteur utile, une **requête HTTP Range de 2 Ko** sur son fichier le plus récent : on décompresse le début du gzip et on lit la 1ʳᵉ ligne (`location`, `lat`, `lon`) ;
  3. on ne lit pas les capteurs que l'API live place aujourd'hui hors d'Allemagne/France.
  Alternatives écartées : `csv_per_month` (100 Go), télécharger un fichier complet par capteur (× 3 en volume pour la même information).
- **Impact** : l'activité est connue à la granularité du **mois** (un capteur actif le 15 est supposé actif le mois entier ; un capteur actif seulement entre deux 15 est manqué). C'est une estimation de faisabilité, recalculée exactement à l'étape 2 à partir des fichiers téléchargés.

### [2026-10-13] Sensor.Community : horodatage en UTC, positions arrondies, humidité saturée
- **Constat** : (1) l'API live affichait `15:17:17` à 15:18 UTC → horodatages en **UTC** ; (2) seuls 34 % des capteurs live ont `exact_location = 1` : les autres ont des coordonnées à 3 décimales (≈ 111 m en latitude) ; (3) sur 3 jours de mars 2024, **55 %** des mesures d'un BME280 et **50 %** de celles d'un DHT22 valent ≥ 99,9 % ; (4) les colonnes `durP1`, `ratioP1`, `durP2`, `ratioP2` (SDS011) et `altitude`, `pressure_sealevel` (BME280) sont toujours vides.
- **Pourquoi** : (2) choix de confidentialité de l'association ; (3) les sondes bon marché saturent dans le brouillard/la condensation, ou sont en panne.
- **Contournement** : (2) sans conséquence pour un appariement à ≥ 0,5 km, mais mentionné ; (3) règle « humidité saturée/figée » prévue à l'étape 3 ; (4) colonnes ignorées au nettoyage.
- **Impact** : la variable la plus utile (l'humidité) sera aussi la plus sale ; la part des heures perdues sera chiffrée dans l'entonnoir.

### [2026-10-13] Cadences différentes entre capteurs
- **Constat** : SDS011 ≈ 1 mesure / 145 s (574 lignes/jour), DHT22 ≈ 1 / 147 s (588), BME280 ≈ 1 / 5 min (280 lignes/jour).
- **Pourquoi** : intervalle de mesure configurable dans le firmware ; certains utilisateurs l'allongent.
- **Contournement** : le seuil de complétude horaire (75 %) sera calculé par rapport à la **cadence propre** de chaque capteur (médiane de ses écarts), pas à une cadence fixe.
- **Impact** : à implémenter et tester à l'étape 3.

### [2026-10-13] EEA : E1a contient déjà 2024 et 2025, un fichier par point avec tout l'historique
- **Constat** : le fichier E1a `SPO.DE_DEBB032_PM2_dataGroup1` couvre 2013-01-01 → 2025-12-31 (113 952 heures, `Verification = 1` partout) ; 2025 est donc déjà **vérifié** (publié en août 2026). Le filtre de dates de l'API est ignoré (même nombre de fichiers pour 2024 et 2025). E2a ne contient que 2026. La docstring du paquet `airbase` (« E1a 2013–2023 ») est périmée.
- **Pourquoi** : l'EEA publie un Parquet par point de prélèvement et l'actualise chaque année.
- **Contournement** : appel direct de l'API REST (`/ParquetFile/urls`) avec `httpx`, puis filtrage de la période dans Polars ; `airbase` reste en dépendance mais n'est pas nécessaire.
- **Impact** : vérité terrain 100 % vérifiée sur toute la période ; ≈ 1,9 Mo par point (dont 11 ans hors période).

### [2026-10-13] EEA : drapeaux, valeurs sentinelles et types
- **Constat** : `Value` est un `Decimal(38,18)` ; les heures manquantes valent **-999** avec `Validity = -1` (607 heures sur 113 952 pour DEBB032) ; `Validity = 2` (1 945 heures) correspond à des valeurs entre 0,6 et 2,0 µg/m³ ; `DataCapture` est toujours vide.
- **Pourquoi** : vocabulaire officiel (dd.eionet) : -1 non valide, -99 maintenance, 1 valide, 2 valide mais sous la limite de détection (valeur = limite), 3 idem remplacée par la moitié de la limite ; Verification 1 vérifié, 2 préliminaire, 3 non vérifié.
- **Contournement** : on garde Validity ∈ {1, 2, 3} et Verification = 1, on exclut -999 explicitement, on convertit `Value` en `Float64`.
- **Impact** : les heures très propres (< 2 µg/m³) sont conservées mais « écrasées » à la limite de détection : à surveiller dans l'analyse des faibles concentrations.

### [2026-10-13] EEA : méthodes non horaires, points sans coordonnées, stations d'outre-mer
- **Constat** : en Allemagne, les métadonnées listent 689 points PM2.5 pour 408 stations, dont 147 préleveurs gravimétriques **journaliers** (`active`). Au niveau européen, 19 points PM2.5 automatiques (dont 1 en France) n'ont **pas de coordonnées** — le premier lancement de la découverte a planté sur cette conversion. Enfin, 25 stations françaises sont **outre-mer** (Antilles, Guyane, Réunion, Mayotte) avec une horloge déclarée UTC−4 à UTC+4.
- **Pourquoi** : réseau réglementaire mêlant méthode de référence journalière et moniteurs automatiques ; métadonnées déclaratives et incomplètes.
- **Contournement** : on garde `Measurement Type = automatic` + fichier horaire E1a + fuseau déclaré ∈ {UTC, UTC+01} (`eea.timezones`) ; les points sans coordonnées sont écartés (conversion non stricte puis `drop_nulls`, cas ajouté aux tests). Après filtrage : **682 points, un seul par station** (382 DE, 300 FR).
- **Impact** : aucune paire perdue (aucun capteur citoyen à moins de 3 km d'une station d'outre-mer) ; une station = une vérité terrain.

### [2026-10-13] Horloge EEA : `heure_UTC = Start − 1 h`, y compris en France malgré les métadonnées
- **Constat** : pour 7 paires capteur ↔ station à moins de 260 m (4 DE, 3 FR), la corrélation entre la moyenne horaire du capteur (UTC) et la station décalée de *k* heures est maximale pour **k = +1 h dans 7 cas sur 7** (r jusqu'à 0,98). Les métadonnées annoncent `UTC+01` pour l'Allemagne mais `UTC` pour la France. Indice concordant : chaque fichier français commence à `2013-01-01 01:00` et contient une seule ligne datée de `2026-01-01 00:00`.
- **Pourquoi** : Allemagne = horloge UTC+1 étiquetée par le début de l'heure (conforme aux métadonnées). France : soit horloge UTC étiquetée par la **fin** de l'heure, soit UTC+1 ; les deux hypothèses donnent la même correction. Hypothèse non tranchée, mais sans effet pratique.
- **Contournement** : règle unique `heure_UTC = Start − 1 h` pour DE et FR ; convention projet = heure étiquetée par son début en UTC. La vérification sera automatisée sur **chaque** paire à l'étape 3 (test + rapport).
- **Impact** : sans ce contrôle empirique, toutes les paires françaises auraient été décalées d'une heure, ce qui aurait dégradé artificiellement le test de transfert.

### [2026-10-13] 54 capteurs à la position (0 ; 0)
- **Constat** : 25 capteurs de particules et 29 sondes d'humidité déclarent `lat = 0, lon = 0` (« Null Island », golfe de Guinée).
- **Pourquoi** : position non renseignée lors de l'enregistrement du capteur.
- **Contournement** : aucun traitement spécial nécessaire : ils ne sont à moins de 3 km d'aucune station, donc jamais appariés.
- **Impact** : nul sur les paires ; à exclure explicitement des statistiques globales si on en fait.

### [2026-10-13] 57 % des capteurs allemands appariés sont près d'une station trafic
- **Constat** : à 1,5 km, 325 des 568 capteurs allemands ont pour station la plus proche une station **trafic** (84 stations), contre 231 une station de fond.
- **Pourquoi** : les stations trafic sont en centre-ville, là où les capteurs citoyens sont les plus denses.
- **Contournement** : on garde toutes les paires mais le type de station devient une variable du modèle et un axe de résultats (erreur par type de station).
- **Impact** : une part de l'écart capteur ↔ station vient de la différence de lieu (bord de route vs balcon), pas du capteur : c'est une **erreur de représentativité** que la correction ne peut pas supprimer. À expliquer dans les conclusions.

### [2026-10-13] Le SDS011 représente 98 % des paires allemandes
- **Constat** : à 1,5 km, 559 des 568 capteurs allemands appariés sont des SDS011 ; en France, 48 sur 62 (plus 11 PMS7003).
- **Pourquoi** : le SDS011 est le capteur du kit historique Sensor.Community.
- **Contournement** (proposé) : modèle centré sur le SDS011 ; les autres types sont exclus de l'entraînement.
- **Impact** : conclusions valables pour le SDS011 uniquement ; le test de transfert français portera sur 48 capteurs.

### [2026-10-13] Parquet « un fichier par jour » plus gros que le gzip
- **Constat** : sur une semaine réelle, un jour SDS011 pèse ≈ 6,3 Ko en gzip, **9,3 Ko** en Parquet zstd écrit seul, mais **5,6 Ko** quand les 7 jours sont dans le même fichier Parquet.
- **Pourquoi** : chaque fichier Parquet porte un en-tête et des métadonnées de schéma ; sur ≈ 575 lignes, ce coût fixe domine.
- **Contournement** : la couche bronze regroupera les jours **par capteur et par mois** (une partition = un fichier).
- **Impact** : ≈ 2,5 Go de bronze estimés à 1,5 km (≈ 5,6 Go à 3 km), dans le budget de 5–10 Go.

---

## Étape 2 — Ingestion à l'échelle (couche bronze)

### [2026-10-14] Le tableau des paires de l'étape 1 comptait des couples, pas des paires
- **Constat** : en Allemagne à 3 km, le tableau annonçait 1 876 « paires » pour 1 269 capteurs. 932 capteurs n'ont qu'une station utilisable à moins de 3 km, mais 392 en ont deux et 125 en ont trois à six : chaque couple (capteur, station) était compté. Le résumé de l'étape 1 affirmait pourtant « une station par capteur » : c'était faux pour `n_pairs` et `n_stations` (le nombre de capteurs, lui, était juste).
- **Pourquoi** : `pair_table` gardait tous les couples à moins du rayon ; seules les colonnes « heures communes » et « humidité » étaient calculées sur la station la plus proche.
- **Contournement** : règle finale **1 capteur = 1 station** : la plus proche *ayant des heures vérifiées pendant l'activité du capteur* (départage par identifiant), distance conservée, nombre de stations à portée gardé dans `n_stations_within`. Test unitaire avec un capteur à portée de deux stations et une station sans données. Notebook 01 corrigé et ré-exécuté.
- **Impact** : nombres de capteurs inchangés, sauf un capteur frontalier compté autrefois dans les deux pays (DE 3 km : 1 269 → 1 268). Stations utilisées : DE 1,5 km 185 → **179**, DE 3 km 253 → **241**, FR 1,5 km 47 → **43**, FR 3 km 84 → **67**.

### [2026-10-14] Décision : télécharger jusqu'à 3 km, entraîner sur ≤ 1,5 km, SDS011 seul
- **Constat** : 1 399 paires SDS011 (DE 559 « near » ≤ 1,5 km + 698 « far » ; FR 48 + 94). **145 stations** ont des capteurs dans les deux bandes (69 seulement « near », 84 seulement « far »).
- **Pourquoi** : la bande 1,5–3 km sert de jeu de test « distance » pour la question business (erreur en fonction de la distance). Comme beaucoup de stations ont des capteurs proches *et* lointains, un découpage naïf mettrait la même station en entraînement et en test.
- **Contournement** : `pairing.radius_download_km = 3.0`, `pairing.radius_train_km = 1.5`, colonne `distance_band` (`near`/`far`) dans la table des paires. À l'étape 6, la validation croisée groupée par station (*GroupKFold*) s'appliquera à **toutes** les distances : une station n'est jamais à la fois en entraînement et en test.
- **Impact** : volume × 2,3 par rapport à 1,5 km (≈ 1,35 million de fichiers capteur-jour) ; le test « distance » aura 698 capteurs en Allemagne.

### [2026-10-14] Limite : les capteurs PMS et SPS sont exclus
- **Constat** : parmi les paires à 3 km, 50 capteurs ne sont pas des SDS011 (FR : 36 PMS7003, 2 PMS5003, 1 SPS30 ; DE : 8 SPS30, 3 PMS).
- **Pourquoi** : optique et algorithme différents (le PMS7003 n'a pas la même réponse à l'humidité) ; trop peu nombreux pour un modèle à part.
- **Contournement** : `pairing.sensor_types = [sds011]` ; ces capteurs ne sont pas téléchargés.
- **Impact** : les conclusions valent **pour le SDS011 seulement** ; le transfert vers la France perd 39 capteurs (22 % des 181 paires françaises).

### [2026-10-14] Limite de représentativité : le type de station suit la donnée jusqu'à gold
- **Constat** : en Allemagne, 317 des 559 paires « near » et 362 des 698 paires « far » ont une station **trafic** (au total, 678 des 1 257 paires allemandes ont une station trafic urbaine) ; en France, 16 sur 48 et 31 sur 94.
- **Pourquoi** : les stations trafic sont en centre-ville, là où les capteurs citoyens sont denses ; elles mesurent en bord de route, pas sur un balcon voisin.
- **Contournement** : `station_type` (trafic, fond, industrielle) et `station_area` (urbaine, périurbaine, rurale) sont conservées dans la table des paires et le seront jusqu'à la couche gold ; **toutes les évaluations seront rapportées par type de station**.
- **Impact** : une partie de l'écart capteur ↔ station est une différence de lieu que la correction ne peut pas supprimer ; elle sera chiffrée séparément pour les stations trafic.

### [2026-10-14] Les fichiers EEA bruts étaient rangés dans `bronze/`
- **Constat** : à l'étape 1, les 381 Parquet EEA (historique 2013–2025, tels que reçus) étaient écrits dans `data/bronze/eea/`.
- **Pourquoi** : raccourci de l'étape de faisabilité.
- **Contournement** : déplacés (sans re-téléchargement) dans `data/raw/eea/`. Bronze EEA = la période d'étude ± 1 jour (marge pour le décalage d'horloge), valeurs et drapeaux inchangés. Politique de rétention : les bruts Sensor.Community, DWD et Météo-France sont supprimés après conversion validée ; les bruts EEA (≈ 0,8 Go) sont gardés car la découverte les relit.
- **Impact** : couches conformes à l'architecture médaillon ; disque final ≈ bronze + 0,8 Go.

### [2026-10-14] Instantané live figé pour que l'inventaire soit reproductible
- **Constat** : la découverte écartait les capteurs que l'API live place *aujourd'hui* hors d'Allemagne et de France ; relancée un autre jour, elle aurait pris un autre instantané.
- **Pourquoi** : l'instantané était mis en cache sous le nom du jour.
- **Contournement** : le premier instantané pris (13/10/2026) est réutilisé par toutes les exécutions suivantes.
- **Impact** : `make data` refait exactement le même plan, quel que soit le jour.

### [2026-10-14] DWD : liste des stations à largeur fixe, en Latin-1, avec une colonne parfois vide
- **Constat** : `TU_Stundenwerte_Beschreibung_Stationen.txt` est un texte à colonnes de largeur fixe, encodé en Latin-1 (« Großenkneten », « Säckingen, Bad (Bergseestr.) ») ; 16 lignes sur 668 n'ont pas la dernière colonne (licence). Les valeurs manquantes valent `-999` ; `MESS_DATUM` est au format `AAAAMMJJHH`, « Stundenwerte in UTC » selon les métadonnées. Le dossier `historical/` contient 2024 et 2025 complets (fichiers jusqu'au 31/12/2025).
- **Pourquoi** : format historique du service météo allemand.
- **Contournement** : expression régulière tolérante (colonne licence optionnelle), testée sur des lignes copiées du vrai fichier ; sentinelles `-999` gardées en bronze et converties en manquant en silver.
- **Impact** : 668 stations lues, aucune perdue ; 222 stations DWD retenues (2 plus proches de chacune des 240 stations allemandes, ≤ 30 km).

### [2026-10-14] Météo-France : un fichier par département, et beaucoup de stations sans humidité
- **Constat** : les données horaires sont publiées par **département** et par période (`H_67_previous-2020-2024.csv.gz`, `H_67_latest-2025-2026.csv.gz`), 200 colonnes. Pour les 58 stations françaises appariées, il faut 59 départements, soit **118 fichiers et ≈ 2,0 Go** compressés, alors qu'on n'en garde que quelques stations. À Paris, 2 stations sur 6 seulement mesurent l'humidité. La Corse est le département `20` (et non `2A`/`2B`). Les noms de fichiers changent d'une année sur l'autre. Le fichier 2025–2026 contient des valeurs seulement « filtrées » (code qualité 9), pas encore validées.
- **Pourquoi** : organisation de la publication ouverte de Météo-France (pas de liste des stations, pas de fichier par station).
- **Contournement** : URL lues dans l'API data.gouv.fr par le titre de la ressource (robuste au changement de nom) ; départements repérés par les centres des ≈ 35 000 communes (un seul appel à geo.api.gouv.fr) situés à moins de 30 km d'une station ; colonnes réduites à l'identité de la station, `T`, `U` et leurs codes qualité ; les 2 stations **ayant de l'humidité** les plus proches sont gardées, puis les bruts sont supprimés.
- **Impact** : 2 Go de pic de disque temporaire pour quelques Mo utiles ; les codes qualité sont conservés pour que silver puisse écarter les valeurs douteuses (code 2).

### [2026-10-14] Même les stations officielles mesurent 100 % d'humidité
- **Constat** : à la station DWD de Großenkneten, **9,6 %** des heures de 2024–2025 sont à ≥ 99,9 %. Sur l'échantillon de janvier 2025, 2 % des heures DWD et 5 % des heures Météo-France sont saturées, contre **48 % (BME280) et 55 % (DHT22)** des mesures des sondes citoyennes. Sur le volume complet 2024–2025 : 1,7 % des heures DWD et 2,8 % des heures Météo-France, contre 30,5 % (BME280) et 53,5 % (DHT22).
- **Pourquoi** : le brouillard et la rosée sont réels ; une humidité de 100 % n'est pas forcément une panne. Ce qui trahit la sonde, c'est d'être à 100 % *quand la station officielle voisine ne l'est pas*.
- **Contournement** : la règle de l'étape 3 reste « humidité saturée ou figée = manquante, jamais une vraie valeur » pour la **sonde** ; l'humidité officielle prend le relais, y compris quand elle vaut 100 %. La correction κ-Köhler n'est jamais appliquée à une humidité saturée ni au-dessus de 95 % (`physics.rh_max_pct`), quelle que soit la source.
- **Impact** : l'humidité officielle n'est pas « meilleure » en brouillard, elle est seulement *vraie* ; ces heures seront peu corrigeables par la physique, et le modèle devra s'appuyer sur d'autres variables.

### [2026-10-14] Le contrat Pandera n'était pas strict (bug de configuration)
- **Constat** : le schéma imprimé dans le notebook 02 affichait `strict=False` alors que le code demandait `strict = True`.
- **Pourquoi** : les options étaient déclarées dans une classe `_Strict` héritée par chaque `Config` ; Pandera ne lit que les attributs déclarés dans la classe `Config` elle-même.
- **Contournement** : `strict = True` et `coerce = False` écrits dans chaque `Config` ; un test vérifie que les cinq contrats sont stricts et rejettent une colonne en trop, un type faux et un identifiant manquant.
- **Impact** : aucune donnée fausse n'était passée (les tables sont construites avec la liste exacte de colonnes), mais le garde-fou ne fonctionnait pas. Toute la couche bronze complète a été validée avec les contrats corrigés.

### [2026-10-14] Le mode `--sample` téléchargeait des fichiers météo de 5 ans
- **Constat** : un premier essai `--sample` (20 capteurs, 1 mois) a téléchargé 613 Mo de fichiers Météo-France, dont le fichier 2020–2024.
- **Pourquoi** : l'échantillon réduisait les capteurs mais pas la période.
- **Contournement** : `Settings.for_sample()` réduit aussi la période au mois de l'échantillon (`sample.month`) ; tout le code en aval (choix des fichiers, filtres bronze) suit automatiquement.
- **Impact** : l'échantillon complet tient en ≈ 20 s de téléchargement et 162 Mo de bruts temporaires.

### [2026-10-14] Bilan du téléchargement complet : 0 échec, 3 h 04, pic de disque ≈ 11 Go
- **Constat** : 45 337 unités capteur × mois (25 326 PM, 20 011 sondes d'humidité) ; 1 354 007 fichiers capteur-jour demandés, 1 208 730 reçus (5,85 Go gzip), **10,7 % absents (404)** ; EEA 582 fichiers (814 Mo), DWD 222 zips (343 Mo), Météo-France 118 fichiers (2,0 Go). **Aucun échec**, ni au téléchargement ni à la conversion. Durée : 3 h 04 de téléchargement (≈ 123 requêtes/s pour Sensor.Community), 12 min 35 s de conversion bronze, 40 s pour les dates d'enregistrement.
- **Pourquoi** : les 404 sont attendus : la fenêtre d'activité est estimée à ± 1 mois près et les capteurs ont des pannes.
- **Contournement** : aucun nécessaire ; les manifestes permettent de relancer sans rien refaire (testé sur les 57 unités rejouées, voir l'entrée « virgule décimale »).
- **Impact** : pic de disque ≈ 11 Go (bruts complets), sous le budget de 20 Go ; état final **5,9 Go** (bronze 5,0 Go, bruts EEA 0,84 Go, le reste < 0,1 Go). 667,7 millions de lignes en bronze.

### [2026-10-14] Certains firmwares écrivent une virgule décimale
- **Constat** : le contrat bronze comptait ≈ 115 000 textes non convertis dans 57 unités. Les capteurs 96465 (SDS011) et 96466 (BME280), fin 2025, écrivent `0,8;;;0,5` à côté de `16;;;4,7` : une **virgule décimale** ; ces valeurs étaient mises à vide.
- **Pourquoi** : notre lecture ne connaissait que le point décimal ; le firmware ou la passerelle de ces capteurs formate les nombres à la française ou à l'allemande.
- **Contournement** : `bronze_frame` remplace la virgule par un point dans les colonnes numériques, compte ces valeurs (`decimal_commas`) et un test reprend les lignes réelles. Les 57 unités ont été marquées « à refaire » dans les deux manifestes (avec la raison), retéléchargées et reconverties en 25 s. Restent ≈ 21 700 textes réellement non numériques (`unavailable`, `unknown`, capteurs 33088/33089), mis à vide et comptés.
- **Impact** : 92 818 mesures récupérées. Sans le compteur d'audit de bronze, ces capteurs auraient semblé « en panne » sur trois mois.

### [2026-10-14] `NaN` n'est pas un vide, et Polars le trouve plus grand que 100
- **Constat** : 1,93 million de mesures d'humidité BME280 valent `NaN` (le texte `nan` dans le CSV), distinct d'une valeur vide. Dans Polars, `NaN >= 99.9` est **vrai** (NaN est classé au-dessus de tous les nombres) : la saturation BME280 était surestimée de 1,7 point.
- **Pourquoi** : la sonde renvoie « pas un nombre » quand la lecture échoue ; Polars suit un ordre total où NaN est le plus grand.
- **Contournement** : `is_saturated` et `reading_status` traitent explicitement `NaN` comme manquant ; `kohler_growth_factor` refuse aussi `NaN` ; tests ajoutés.
- **Impact** : piège à retenir pour toutes les couches : toute comparaison sur une colonne flottante doit exclure `NaN`.

### [2026-10-14] Humidité figée : DHT22 bloquées à 1,0 %, BME280 à 0 %, HTU21D aux bornes de leur formule
- **Constat** : 11,2 millions de mesures DHT22 valent **exactement 1,0 %** (166 sondes, 6,7 % des mesures DHT22) ; 0,94 million de mesures BME280 valent exactement 0 % (63 sondes ; sur ces lignes, température moyenne −33 °C et pression 303 hPa) ; HTU21D bloquées à 118,99 % ou −6 % (4 sondes sur 5) ; BME280 à 255 ou 655,35. Les stations officielles, elles, ne mesurent moins de 5 % que **9 heures sur 5,27 millions**.
- **Pourquoi** : composant bloqué ou ne répondant plus : la DHT22 renvoie son plancher, la BME280 des registres vides, la HTU21D les deux bornes de sa formule de conversion (−6 + 125 × x), la BME280 un débordement d'entier.
- **Contournement** : chaque mesure reçoit un statut (`transform/humidity.py`, `reading_status`) : manquante (vide ou `NaN`), hors bornes (< 0 ou > 100 %), **figée basse** (< `humidity.floor_pct` = 5 %), **saturée** (≥ 99,9 %), utilisable. Seule la dernière est une mesure ; la règle de l'étape 3 « saturée ou figée = manquante » s'applique aux trois défauts.
- **Impact** : part des mesures utilisables : **DHT22 39,7 %**, BME280 68,6 %, HTU21D 55,1 %, SHT30/31/85 91–99 %. Les sondes Sensirion sont fiables mais rares (21 sondes sur 1 127). Une humidité constante sur plusieurs heures à l'intérieur de la plage plausible (1,6 % des heures DHT22) sera traitée à l'étape 3.

### [2026-10-14] Saturation de l'humidité sur 2024–2025 : par type, par mois, selon l'âge
- **Constat** : mesures ≥ 99,9 % : **DHT22 53,5 %**, **BME280 30,5 %**, SHT31 8,7 %, SHT30 1,5 %, SHT85 0,6 %. Elle suit la saison humide : DHT22 ≈ 66 % d'octobre à décembre 2024 contre 35 % en mai 2025, BME280 ≈ 43–49 % en hiver contre 19–25 % au printemps. Les stations officielles voisines ne dépassent jamais 9 % (Météo-France, novembre 2024 ; 1,7 % des heures DWD et 2,8 % des heures Météo-France sur la période). Selon l'âge de la sonde, la part de mois « morts » (voir l'entrée suivante) passe pour la DHT22 de 47–50 % la première année à **75–78 % après 4 ans**, et pour la BME280 de **19 %** la première année à 40–49 % ensuite.
- **Pourquoi** : les sondes se mouillent par temps humide (l'humidité officielle médiane est de 85 % quand elles saturent) et ne sèchent plus ; avec les années, le capteur capacitif se dégrade.
- **Contournement** : âge estimé par l'identifiant du capteur (courbe d'enregistrement tirée de 16 listings d'archive, toutes les sondes datées) ; l'âge pourra servir de variable.
- **Impact** : l'échantillon d'un seul mois (janvier) surestimait la saturation (48 % BME280) : la réalité est saisonnière. L'humidité officielle sera la règle plutôt que l'exception, surtout en hiver et pour les vieilles DHT22.

### [2026-10-14] Sondes « mortes » ou défauts épisodiques : seuil X = 0,3
- **Constat** : pour chaque mois-sonde, $s_{\text{mois}}$ = part des heures en défaut (plus de la moitié des mesures de l'heure non utilisables). Sur 18 744 mois-sondes, 24 % n'ont aucune heure en défaut et 27,8 % en ont plus de 90 % ; la distribution n'est **pas** nettement en deux blocs (contrairement à l'échantillon), son creux est entre 10 et 50 %. Contre la station officielle, les heures utilisables des mois où $0 < s_{\text{mois}} < 0{,}3$ sont crédibles (BME280 : biais médian −2,3 à +1,0 point, corrélation 0,82–0,83), mais dès 0,3 le biais grimpe (DHT22 +13,8 points puis +21,9 dans la tranche 0,5–0,6 ; BME280 +2,7 puis +5,7) et la corrélation DHT22 tombe sous 0,4.
- **Pourquoi** : une sonde qui commence à mourir ne se contente pas de saturer : même ses valeurs « normales » dérivent vers le haut.
- **Contournement** : `humidity.dead_month_share = 0.3` : au-delà, tout le mois est « sans humidité locale fiable » ; en dessous, seules les heures en défaut sont écartées. Sensibilité chiffrée dans le notebook 02 (part récupérable de 32,6 % à X = 0,1 à 47,4 % à X = 0,7).
- **Impact** : répartition des 1 126 sondes ayant des mesures : **DHT22** 118 saines, 141 mortes certains mois, **368 mortes toute la période (59 %)** ; **BME280** 150 saines, 230 mortes certains mois, 93 mortes toute la période (20 %) ; Sensirion 15 saines sur 21, aucune morte toute la période. Heures : 11,99 millions avec une mesure, 7,10 M perdues dans les mois morts, 0,22 M écartées comme défauts épisodiques, **4,66 M récupérables (38,9 %)** ; avec X = 0,5 on en garderait 43,4 %, au prix de 0,54 M d'heures douteuses.

### [2026-10-14] Preuve : quand la sonde sature, l'air ne l'est presque jamais
- **Constat** : sur 11,5 millions d'heures sonde ↔ station météo officielle de rang 1, 5,11 millions sont des heures où la sonde est saturée. La station officielle n'est alors saturée que **2,1 %** du temps ; elle mesure moins de 90 % dans **63 %** des cas, moins de 80 % dans 35 % ; médiane 85 %. Figure `reports/figures/02_saturation_vs_officiel.png`.
- **Pourquoi** : c'est une panne de sonde, pas du brouillard.
- **Contournement** : règle de l'étape 3 confirmée : humidité saturée ou figée = manquante, humidité officielle en secours.
- **Impact** : utiliser l'humidité de la sonde sans ce filtre aurait injecté 100 % d'humidité dans plus de la moitié des heures, et la correction κ-Köhler aurait divisé les particules par un facteur qui diverge.

### [2026-10-14] Même une sonde « saine » lit trop sec
- **Constat** : dans les mois-sondes **sans aucune** heure en défaut, l'humidité de la sonde est en médiane **13 points sous** l'humidité officielle pour la BME280 (corrélation 0,68) et 9,6 points sous pour la DHT22 (corrélation 0,52).
- **Pourquoi** : probablement l'échauffement du boîtier par l'électronique et le capteur de particules (une température plus haute donne une humidité relative plus basse), et des sondes montées près d'un mur ou sous un toit ; la DHT22 est en plus peu précise.
- **Contournement** : aucun en bronze ; l'humidité locale « fiable » n'est pas forcément *juste*. L'étape 5 comparera trois variantes : humidité locale seule, officielle seule, et locale corrigée par l'officielle.
- **Impact** : le drapeau « humidité locale fiable » dit « pas en panne », pas « exacte » ; la variable humidité du modèle sera choisie sur les résultats.

### [2026-10-14] Humidité de secours : distance capteur ↔ station météo, une variable et une limite
- **Constat** : toutes les paires ont au moins une station météo officielle à moins de 30 km de leur station EEA. Distance capteur → station météo de rang 1 : médiane **6,4 km** (DWD) et **6,5 km** (Météo-France), 90 % des paires sous 12,8 km et 9,2 km, maximum 26,7 km. Pour **70 paires**, la station de rang 2 est plus proche du capteur que celle de rang 1.
- **Pourquoi** : les stations météo sont choisies par leur distance à la station EEA (pour être communes à tous les capteurs d'une station), mais le capteur peut être jusqu'à 3 km plus loin. L'humidité relative varie peu sur quelques dizaines de km en plaine, mais pas nulle part : relief, cours d'eau, îlot de chaleur urbain.
- **Contournement** : `bronze/reference/pair_weather.parquet` garde, pour chaque paire, les deux stations météo avec leur distance au capteur et à la station EEA ; la distance capteur ↔ station météo deviendra une variable du modèle, et silver pourra prendre la station la plus proche du capteur quand les deux ont des données.
- **Impact** : limite à documenter dans les résultats : l'erreur du modèle sera aussi rapportée selon cette distance.

### [2026-10-14] Horloge des stations météo : UTC vérifié, et un premier test trompeur
- **Constat** : corrélation décalée (pas de 10 min) entre la température officielle et celle des sondes des capteurs (horloge UTC vérifiée). Un premier essai sur 12 sondes et 2 mois donnait pour Météo-France +70 min en janvier et +30 min en juillet, ce qui ressemblait à un problème d'horloge. Refait sur 4 mois × 2 ans, sondes à moins de 12 km, mois avec r > 0,85 : décalage médian DWD −10 / 0 min, Météo-France +20 / +20 min (hiver / été), et surtout un écart hiver − été, sonde par sonde, de **0 min** (Météo-France, 25 sondes) et **5 min** (DWD, 15 sondes).
- **Pourquoi** : une horloge en heure légale ferait sauter le décalage d'une heure entre l'hiver et l'été ; il ne saute pas. Les +20 min de Météo-France viennent probablement de la façon dont la valeur horaire est mesurée et de l'inertie du boîtier des sondes. Le premier essai avait trop peu de sondes.
- **Contournement** : test robuste intégré au notebook 02 (section 6.1) ; `time_utc` des deux services utilisé tel quel.
- **Impact** : pas de correction d'horloge pour la météo. Leçon de méthode : ne pas conclure sur une poignée de sondes.

### [2026-10-14] Capteurs déplacés, dont un qui était au Viêt Nam
- **Constat** : 65 capteurs PM sur 1 399 ont plusieurs positions dans la période : 23 de moins de 100 m (coordonnées corrigées), 21 de 0,1 à 3 km, **21 de plus de 3 km**. Le capteur 84804 était **à Hanoï** jusqu'au 5 septembre 2025 avant d'être installé à Magdebourg ; le 36947 a eu pendant 11 heures une position en Russie (erreur de saisie probable).
- **Pourquoi** : les capteurs citoyens sont déménagés, prêtés, reconfigurés ; la position est celle déclarée par le propriétaire.
- **Contournement** : la paire a été calculée sur la position la plus récente ; l'étape 3 découpera les mesures par position (`transform/positions.py`) et ne gardera que les périodes où le capteur est à portée de sa station.
- **Impact** : sans ce découpage, 20 mois de pollution d'Hanoï auraient été comparés à une station de Magdebourg.

### [2026-10-14] `sensor_type` en majuscules dans le contenu, en minuscules dans les noms de fichiers
- **Constat** : les fichiers s'appellent `…_sds011_sensor_…csv.gz`, mais la colonne `sensor_type` contient `SDS011`, `DHT22`, `BME280`.
- **Pourquoi** : conventions différentes entre l'archive et le firmware.
- **Contournement** : bronze garde le contenu tel quel ; silver normalisera en minuscules (comme les tables de référence).
- **Impact** : nul si on le sait ; une jointure naïve sur `sensor_type` aurait tout perdu.

## Étape 3 — Nettoyage (couche silver)

### [2026-10-14] Décision : l'humidité officielle devient l'entrée principale
- **Constat** : seules 38,5 % des heures-sondes sont fiables, 467 sondes sur 1 126 n'ont aucune heure fiable, et même les sondes saines lisent trop sec (−13 points pour la BME280). L'humidité officielle (DWD, Météo-France) existe pour 100 % des paires appariées.
- **Pourquoi** : une variable clé ne peut pas reposer sur une source absente deux fois sur trois et biaisée le reste du temps.
- **Contournement** : `official_humidity` (station officielle la plus proche du capteur) est l'entrée principale ; `local_humidity` n'est renseignée que si elle est fiable (`local_reliable`), comme variable secondaire. L'étape 5 comparera les deux.
- **Impact** : argument métier : **la correction n'exige pas de sonde d'humidité fonctionnelle**, la station météo publique suffit. Dans les heures-paires utilisables, la sonde locale n'est fiable que 27 % du temps en Allemagne et 32 à 46 % en France.

### [2026-10-14] Aucune ligne supprimée : un statut par mesure, et l'entonnoir calculé à partir des statuts
- **Constat** : un nettoyage qui supprime des lignes ne peut pas dire après coup pourquoi une heure manque.
- **Pourquoi** : la note dépend de la traçabilité du nettoyage.
- **Contournement** : chaque mesure SDS011 (`quality_flag`), heure de référence, heure météo, heure-sonde et heure-paire (`status`) porte `ok` ou la *première* règle qu'elle viole, dans un ordre fixe ; `transform/funnel.py` compte, règle après règle, les lignes, capteurs, paires et stations perdus ; `reports/funnel.md` est régénéré à chaque `pair`. Les contrats Pandera silver refusent tout statut inconnu. Un premier rapport comptait « 1 » ligne par règle (un `pl.lit(1).sum()` qui ne se répète pas dans un `group_by`) : corrigé et couvert par un test avant publication.
- **Impact** : les 377,95 millions de mesures SDS011 sont dans `silver/sensor_community/pm/` avec leur statut (2,6 Go, autant que bronze).

### [2026-10-14] Particules : uniquement des règles physiques et de cohérence
- **Constat** : 2,1 % des mesures SDS011 violent une règle. La saturation a une signature précise : PM2.5 = 999,9 **avec** PM10 = 1 999,9 (2,6 M mesures, 360 capteurs). Le firmware écrit −1/−1 en cas d'erreur (0,45 M, 10 capteurs). PM2.5 > PM10 vient à 99,9 % de 2 capteurs (0,79 M). Les dépassements de la plage du SDS011 (> 999,9 µg/m³, 0,2 M) touchent 565 capteurs, avec des pics l'été 2024 (pas au Nouvel An : 0,4 %). Les « doublons » sont surtout des lignes vides jumelles d'une vraie mesure ; 3 286 mesures ont le même horodatage avec des valeurs différentes.
- **Pourquoi** : pannes et codes du firmware ; PM2.5 est une partie de PM10.
- **Contournement** : règles dans l'ordre `pm_missing`, `duplicate` (la 1re copie gardée), `duplicate_conflict` (toutes écartées), `pm_negative`, `pm_saturated`, `pm_above_range`, `pm25_above_pm10`, `pm_stuck`. **Aucun filtre statistique** (z-score, IQR) : un pic à 850 µg/m³ dans de l'air propre reste `ok` (test dédié). Seuils dans `particles.*`.
- **Impact** : aucune règle ne supprime à elle seule plus de 2 capteurs ; les pics par forte humidité, que le modèle doit apprendre à corriger, sont intacts.

### [2026-10-14] Capteurs figés : le seuil d'une heure, justifié par la station
- **Constat** : les longues séries de valeurs identiques sont toutes des valeurs de panne (0/0, moins de 1 µg/m³, −1, 999,9), pendant que la station de référence mesure 5 à 7 µg/m³ en médiane. Une valeur ≥ 1 µg/m³ ne se répète presque jamais une heure (14 séries de 1 à 3 h sur 6 capteurs en deux ans).
- **Pourquoi** : un compteur optique dans l'air réel fluctue d'une mesure à l'autre ; une valeur figée est un capteur qui ne mesure plus (ventilateur ou laser).
- **Contournement** : `particles.stuck_min_hours = 1.0` : (PM10, PM2.5) identiques pendant ≥ 1 h, sans trou de plus d'1 h → `pm_stuck` (3,07 M mesures, 242 capteurs).
- **Impact** : les séries courtes à 0/0 ou < 1 µg/m³ (≈ 0,96 M mesures) sont gardées : en air propre, un capteur à 0,1 µg/m³ de résolution peut répéter une petite valeur. Limite documentée : ce sont peut-être déjà des pannes naissantes.

### [2026-10-14] Complétude horaire selon la cadence propre de chaque capteur
- **Constat** : la cadence médiane est de 146 s, mais environ 250 capteurs envoient à 60, 120, 180, 240 ou 300 s ; quelques-uns changent de cadence en cours de période.
- **Pourquoi** : réglage du firmware par le propriétaire.
- **Contournement** : cadence = médiane des écarts entre mesures consécutives (coupures > 1 h ignorées), par capteur et par mois ; complétude = mesures valides / (3600 / cadence) ; seuil 75 % (`particles.min_completeness`). Chaque heure garde moyenne, médiane, écart-type et nombre de mesures valides (PM2.5 et PM10).
- **Impact** : 93,3 % des 15,67 M heures-capteur sont complètes, 5,2 % incomplètes, 1,5 % sans mesure valide (raison dominante : saturation 45 %, capteur figé 37 %).

### [2026-10-14] Capteurs déplacés : un appariement par segment de position
- **Constat** : 65 capteurs ont plusieurs positions (1 479 segments). 12 segments (10 capteurs, 1,3 M mesures) avaient, à leur ancienne adresse, une autre station plus proche ; 22 segments (19 capteurs, 1,0 M mesures) n'avaient aucune station à 3 km : capteur 84804 à Hanoï, 42694 à Bruxelles jusqu'en avril 2024, d'autres en périphérie.
- **Pourquoi** : la paire de l'étape 2 était calculée sur la position la plus récente.
- **Contournement** : segment = suite continue de mesures au même site et aux mêmes coordonnées (revenir à une ancienne position ouvre un nouveau segment) ; chaque segment est apparié avec la même règle qu'à l'étape 1 (`nearest_usable_station`, factorisée et partagée avec la découverte). Une paire est désormais `(capteur, segment) → station`, identifiée par `pair_id`.
- **Impact** : 42 902 heures-capteur sans station gardent le statut `no_reference_station` au lieu d'être comparées à une station lointaine. Les 12 stations de remplacement étaient déjà téléchargées (aucun téléchargement supplémentaire).

### [2026-10-14] Référence EEA : les petites valeurs négatives validées sont gardées
- **Constat** : 8 955 heures PM2.5 validées sont négatives (0,18 %, 36 points, médiane −0,79, minimum −6,2) ; une seule passe sous −5 µg/m³. Les codes −1 et −99 vont toujours avec la sentinelle −999. Aucune heure non vérifiée dans la période.
- **Pourquoi** : bruit de mesure des analyseurs automatiques autour de zéro, accepté par l'agence.
- **Contournement** : valeurs ≥ `eea.negative_floor_ugm3` (−5) gardées **telles quelles** ; en dessous, `negative_beyond_uncertainty`. Pas de remplacement par 0.
- **Impact** : remplacer par 0 ou retirer ces heures aurait biaisé la cible vers le haut en air propre ; 2 heures écartées en tout (PM2.5 et PM10).

### [2026-10-14] Contrôle d'horloge : les niveaux trompent, les variations non
- **Constat** : le premier contrôle automatique (corrélation des **niveaux**, meilleur décalage ≠ 0 → exclusion) rejetait 4 paires sur 20 de l'échantillon et 171 sur 1 440 au complet. Exemple : la paire 32200, excellente, donnait 0,920 à 0 h et 0,921 à +1 h sur janvier 2025. Les paires rejetées avaient presque toutes des corrélations très faibles : le « meilleur » décalage y était du bruit.
- **Pourquoi** : les niveaux de pollution varient lentement, la courbe de corrélation est plate autour de 0 et son maximum dépend du bruit ou du trajet d'un panache entre station et capteur.
- **Contournement** : corrélation des **variations d'une heure à l'autre** (pic net au vrai décalage) ; verdict `clock_unverifiable` si < 500 heures ou meilleure corrélation < 0,1, `clock_mismatch` si le meilleur décalage ≠ 0 **et** bat le décalage 0 d'un z de Fisher ≥ 3 (`clock.*`). Même test **par station**, toutes ses paires regroupées (les horodatages des capteurs viennent tous du même serveur : un problème d'heure est un problème de station). Une paire est exclue si elle ou sa station est en décalage. **Contre-épreuve** : sans la correction `Start − 1 h`, le test rejette 1 085 paires vérifiables sur 1 142 et 207 stations sur 216.
- **Impact** : avec la correction, 29 paires et 7 stations en décalage ; 64 paires exclues (615 040 heures, 4,4 % des heures alors restantes).

### [2026-10-14] Les stations TEOM répondent avec environ une demi-heure de retard
- **Constat** : 5 des 7 stations en décalage mesurent avec un **TEOM** : DESN020, DESN025, DESN045, DESN061 en Saxe et FR33220 (TEOM-FDMS). Toutes à +1 h, mais la corrélation est **répartie** entre 0 et +1 h (DESN061 : 0,089 et 0,108) au lieu de chuter à 0 comme pour un vrai décalage, et c'est pareil en hiver et en été. DEBY099 (+1 h net, z = 13, un seul capteur) et DEMV007 (−1 h, z = 3,7) restent inexpliquées.
- **Pourquoi** : probablement la réponse retardée de la microbalance (moyennes glissantes de l'instrument), pas une erreur d'horloge ni l'heure d'été.
- **Contournement** : la règle décidée s'applique : leurs 52 paires sont exclues (`clock_mismatch_station`). Un décalage d'une demi-heure ne peut pas être corrigé proprement à l'échelle horaire.
- **Impact** : la Saxe perd 50 de ses 88 paires ; à rediscuter à l'étape 4 (garder ces stations avec une variable « méthode de mesure » est une option).

### [2026-10-14] 298 paires « invérifiables » gardées
- **Constat** : pour 298 paires (2,25 M heures), les variations horaires du capteur ne suivent pas la station (corrélation médiane 0,04), souvent des capteurs lointains ou bruités.
- **Pourquoi** : un capteur bruité ou une station de type différent (trafic contre fond) n'a pas de variations communes exploitables pour lire un décalage.
- **Contournement** : pas d'exclusion (rien ne prouve un problème d'heure, et l'horloge des capteurs est celle du serveur) ; le drapeau `clock_flag` est conservé.
- **Impact** : limite à suivre à l'étape 4 : ces paires seront probablement les moins bien corrigées.

### [2026-10-14] Sondes d'humidité : valeurs figées pendant 3 heures ou plus
- **Constat** : certaines sondes répètent exactement la même humidité utilisable (souvent 98,7 %) pendant des heures. Pendant les séries de ≥ 3 h, l'humidité officielle voisine varie de 6 à 50 points en médiane, contre 2 points pour les séries de moins de 2 h.
- **Pourquoi** : sonde bloquée juste sous la saturation.
- **Contournement** : statut `stuck` si une valeur utilisable est identique pendant ≥ `humidity.stuck_min_hours` (3 h) ; 16 sites ont deux sondes, la plus fiable selon `humidity.probe_preference` est retenue.
- **Impact** : 0,86 M mesures en plus écartées ; 4,62 M heures-sondes fiables sur 11,99 M (38,5 %).

### [2026-10-14] Station météo : la plus proche du capteur, avec une station de secours heure par heure
- **Constat** : pour 74 paires, la station officielle la plus proche du capteur n'est pas la plus proche de la station EEA. La station principale manque d'humidité 5 % des heures en Allemagne.
- **Pourquoi** : les stations météo avaient été choisies depuis la station EEA, jusqu'à 3 km du capteur.
- **Contournement** : station principale = la plus proche du **capteur** ; deux stations à moins de 0,5 km d'écart (`weather.tie_km`) sont départagées par la complétude ; une heure manquante est prise à l'autre station (`weather_fallback`, distance de la station réellement utilisée dans `sensor_weather_km`).
- **Impact** : 3 339 heures seulement sans humidité officielle ; distance médiane capteur → station météo 6,4 km.

### [2026-10-14] Bilan silver : 13,28 millions d'heures-paires utilisables
- **Constat** : des 15,67 M heures-capteur, il reste 13,28 M heures-paires utilisables (84,7 %) : 1 379 paires, 1 330 capteurs, 290 stations. DE near 5,34 M (556 paires, 171 stations), DE far 6,86 M (680 paires, 185 stations), FR near 0,40 M (48 paires), FR far 0,68 M (95 paires). 139 stations ont des capteurs utilisables dans les deux bandes.
- **Pourquoi** : —
- **Contournement** : `cv_group` (= code de la station) est porté par chaque heure-paire pour la validation croisée groupée (étape 6).
- **Impact** : la bande « far » est plus riche que la bande « near » ; le test distance aura beaucoup de données. Les stations trafic font 53 % des heures allemandes.

### [2026-10-14] Disque : 9,8 Go, proche du plafond final de 10 Go
- **Constat** : bronze 5,0 Go, silver 3,9 Go (dont 2,6 Go de mesures SDS011 avec statut), bruts EEA conservés 0,85 Go.
- **Pourquoi** : garder un statut par mesure double le volume des mesures brutes.
- **Contournement** : à l'étape 5, la couche gold ne gardera que les colonnes utiles des heures-paires `ok` ; si besoin, les bruts EEA (relus seulement par la découverte) pourront être supprimés.
- **Impact** : budget à surveiller ; pic du calcul silver sous 11 Go, loin des 20 Go.

### [2026-10-14] Décision : les stations TEOM sont gardées, la méthode de mesure devient une colonne
- **Constat** : 5 des 7 stations signalées par le contrôle d'horloge mesurent avec un TEOM ; leur décalage (+1 h, corrélation répartie entre 0 et +1 h, identique hiver comme été) est la réponse retardée de l'instrument, pas une horloge fausse. À l'étape 4, leurs paires valent les paires vérifiées (R² médian 0,59, 41 % passent les cibles EPA en journalier).
- **Pourquoi** : la microbalance TEOM rend une valeur lissée et décalée d'environ une demi-heure.
- **Contournement** : `method_class` (optique, néphélomètre + bêta, bêta, TEOM, inconnue), tirée du champ « Measurement Method » de l'EEA (`eea.method_classes`), est portée par chaque paire ; un décalage expliqué par la méthode (`clock.instrument_lag_h: {teom: 1}`) devient `instrument_lag` et la paire est **gardée**. DEBY099 et DEMV007, inexpliquées, restent exclues.
- **Impact** : +544 000 heures-paires utilisables (13,28 → 13,82 M) ; la méthode sert de variable et d'axe de lecture des résultats.

### [2026-10-14] La règle « valeurs figées ≥ 1 h » visait trop l'air propre : révisée
- **Constat** : sur les heures couvertes par un plateau d'au moins une heure, 36,9 % ont une valeur EEA sous 5 µg/m³ (14,7 % sous 3), contre 29,7 % (11,1 %) pour les heures gardées : les heures propres étaient sur-représentées.
- **Pourquoi** : en air calme et propre, un SDS011 (résolution 0,1 µg/m³) peut légitimement répéter 0,3 ou 0,5 µg/m³ pendant une heure.
- **Contournement** : un plateau ≥ 1 h n'est « figé » que si c'est un **code de panne connu** (0/0 ; −1 et 999,9 sont déjà écartés par les règles précédentes, `particles.stuck_codes`) **ou** si la station de référence a varié d'au moins 5 µg/m³ pendant ce temps (`particles.stuck_reference_range_ugm3`, même borne que le bruit des analyseurs). Pour l'appliquer, les segments et leurs stations sont désormais calculés **avant** le marquage des mesures (`silver/segments.py`).
- **Impact** : 12 163 plateaux en air calme (0,51 M mesures, 42 % sous 5 µg/m³) sont gardés ; les 746 plateaux « la station a varié » ont la distribution des heures gardées (31 % sous 5 µg/m³). Les plateaux 0/0 restent figés et penchent encore un peu vers l'air propre (36 %), mais la station mesure alors 6,3 µg/m³ en médiane : le capteur ne voit plus rien. Mesures figées : 3,07 → 2,56 M.

### [2026-10-14] Disque : le statut en `Enum` ne fait rien gagner, la suppression des bruts EEA oui
- **Constat** : la colonne `quality_flag` pèse 1,0 Mo sur 2,82 Go de mesures (0,04 %), en `Categorical` comme en `Enum` : Parquet la stocke déjà sous forme de dictionnaire. Le volume vient de l'horodatage (1,48 Go) et des valeurs PM10/PM2.5 (1,34 Go).
- **Pourquoi** : codage par dictionnaire et compression zstd de Parquet.
- **Contournement** : `quality_flag` et le `status` des heures-paires sont tout de même des `pl.Enum` (liste fermée : un statut inconnu ne peut pas être écrit). Les bruts EEA sont supprimés une fois en bronze (`eea.keep_raw: false`) ; la découverte réutilise sa table de disponibilité mise en cache dans `data/meta` au lieu de relire les bruts, et le téléchargement saute les points déjà convertis. Le notebook 01 retélécharge à la volée le seul fichier brut qu'il montre.
- **Impact** : `data/` passe de **10,28 à 9,43 Go** (bruts EEA 858 Mo → 0,2 Mo) ; mesures silver 2,76 → 2,76 Go (inchangé).

### [2026-10-14] Le « avant » : sans correction, 20 % des paires allemandes passent les cibles EPA en horaire
- **Constat** : cibles EPA (R² ≥ 0,70, pente 1 ± 0,35, ordonnée −5 à +5 µg/m³, RMSE ≤ 7 µg/m³), capteur brut régressé sur la référence : **Allemagne 19,9 % des paires en horaire, 32,3 % en journalier ; France 11,8 % et 17,9 %**. Sans les paires à l'horloge invérifiable : 23,7 % / 38,0 % (DE), 15,8 % / 24,2 % (FR). La pente est la cible la plus manquée (médiane ≈ 0,58 : le capteur voit environ 60 % de la référence).
- **Pourquoi** : le SDS011 sous-estime les fines particules en air sec (ses tailles et son étalonnage ne sont pas ceux des instruments de référence) ; le bruit horaire pénalise le R².
- **Contournement** : aucun ; c'est le point de départ chiffré du README et la référence de l'étape 7.
- **Impact** : le modèle sera jugé sur la part de paires qu'il fait passer de l'autre côté, en horaire et en journalier.

### [2026-10-14] L'humidité explique l'essentiel du biais ; l'erreur est multiplicative
- **Constat** : le rapport médian capteur / référence passe de 0,42 (humidité officielle < 50 %) à 0,79 (≥ 95 %) en Allemagne, à chaque saison. Il reste entre 0,5 et 0,6 à tous les niveaux de pollution, alors que l'erreur absolue médiane passe de −1,5 µg/m³ (< 5 µg/m³) à −29 µg/m³ (≥ 50 µg/m³) ; la dispersion relative (écart-type du log du rapport) reste stable (0,76–0,94).
- **Pourquoi** : la croissance hygroscopique fait « rattraper » la référence en air humide ; un capteur qui lit une fraction à peu près fixe de la référence produit une erreur proportionnelle au niveau.
- **Contournement** : proposition pour l'étape 6 : modéliser en échelle log (ln référence en fonction de ln capteur, humidité…), à comparer à l'échelle µg/m³ sur les mêmes plis.
- **Impact** : en µg/m³, quelques heures polluées domineraient l'erreur quadratique (RMSE souvent plusieurs fois la MAE).

### [2026-10-14] Pas de dérive moyenne, mais des capteurs instables
- **Constat** : pour 830 paires suivies deux ans, le rapport 2025 / 2024 a une médiane de 1,006, mais 28 % des paires changent de plus de 20 %. Les capteurs les plus anciens lisent un peu plus haut (0,61 après 6 ans contre 0,49 la première année) ; ceux de 4 à 6 ans ont la RMSE la plus forte.
- **Pourquoi** : encrassement, changement d'installation, fin de vie du ventilateur ou du laser.
- **Contournement** : l'âge du capteur devient une variable candidate ; l'étape 6 mesurera l'erreur sur 2025 d'un modèle appris sur 2024.
- **Impact** : une correction figée dans le temps se dégradera pour une partie des capteurs ; limite à documenter.

### [2026-10-14] Paires « invérifiables » : des capteurs qui ne suivent pas la référence
- **Constat** : 235 paires invérifiables (≥ 500 h) ont un R² médian de 0,03, une pente médiane de 0,17 ; aucune ne passe les cibles EPA en horaire (1,8 % en journalier), contre 23,1 % et 36,8 % pour les paires vérifiées, à composition égale (bande, type de station, distance). Elles font 79 % des 10 % pires paires ; certains capteurs affichent 190 à 640 µg/m³ en moyenne pour 7 à 11 µg/m³ réels sans jamais violer une règle physique.
- **Pourquoi** : capteurs défectueux (optique sale, ventilateur, électronique) dont les valeurs restent dans la plage du SDS011 ; le contrôle d'horloge, qui teste si les variations suivent la station, les détecte.
- **Contournement** : gardées avec leur drapeau (décision du commanditaire) ; **recommandation** : les exclure de l'entraînement et les évaluer à part ; test avec et sans elles à l'étape 6.
- **Impact** : avec elles, le « avant » perd 4 à 6 points (DE horaire 19,9 % contre 23,7 %).

## Étape 5 — Variables du modèle (couche gold)

### [2026-10-14] Décision : paires invérifiables exclues de l'entraînement, chaque chiffre clé donné deux fois
- **Constat** : les 301 paires « invérifiables » (2,32 M heures utilisables) sont, pour l'essentiel, des capteurs défectueux (R² médian 0,03).
- **Pourquoi** : elles sont repérées grâce à la station de référence, qui n'existe pas en service ; les exclure du test rendrait les résultats un peu optimistes.
- **Contournement** : `sane_pair` (horloge vérifiée ou retard d'instrument) ; la zone d'entraînement = Allemagne, ≤ 1,5 km, paires saines (4,67 M heures, 459 paires). Les paires non saines restent dans gold pour être évaluées à part. 2 paires (14 heures) n'avaient jamais pu passer le contrôle d'horloge : elles comptent comme non saines.
- **Impact** : à partir de l'étape 6, chaque chiffre clé est donné pour les « paires saines » **et** pour « toutes les paires ».

### [2026-10-14] Changement du modèle physique : un gain et une hygroscopicité, ajustés ensemble
- **Constat** : le capteur brut **sous-estime** la référence (rapport 0,42 en air sec, 0,79 en air saturé). La correction κ-Köhler seule divise la mesure par $C(\mathrm{HR}) \ge 1$ : elle aggraverait l'écart.
- **Pourquoi** : κ-Köhler suppose un capteur exact en air sec ; le SDS011 ne voit qu'environ 42 % de la masse de la référence (hypothèse : il rate les plus fines particules, alors que la référence sèche et pèse l'échantillon).
- **Contournement** : $\mathrm{PM}_{\text{réf}} \approx a\,\mathrm{PM}_{\text{brut}}/C(\mathrm{HR})$, soit $\ln \mathrm{PM}_{\text{réf}} = \ln a + \ln \mathrm{PM}_{\text{brut}} - \ln C$ ; pour chaque κ le meilleur $\ln a$ est une moyenne, κ est cherché sur une grille puis par section dorée (`transform/physics.py`, test qui retrouve $a$ et κ connus). Ajusté **sur l'entraînement uniquement**, une fois par pli (sans la station testée) et une fois sur toute la zone (pour la France) ; l'estimation est donc **hors pli**.
- **Impact** : $a$ = 2,34–2,47 et κ = 0,064–0,068 selon les plis (zone entière : 2,39 et 0,066). $1/a = 0{,}42$ = le rapport observé en air sec : plausible. κ est bas pour un aérosol continental (0,1–0,4, Petters & Kreidenweis 2007) : c'est un κ **effectif** (hypothèses : le capteur chauffe l'air aspiré, l'humidité vient d'une station à ~6 km, la réponse optique ne suit pas toute la croissance). Après correction, le rapport médian passe de 0,41–0,79 à 0,96–1,15 ; la forme de Köhler (plate puis brutale) sous-estime l'effet entre 80 et 95 %. L'humidité n'améliore l'erreur en log que de 6 % : l'essentiel de l'erreur reste à apprendre.

### [2026-10-14] L'humidité officielle est plafonnée à 95 % dans le modèle physique
- **Constat** : avec l'humidité officielle, 100 % est réel (brouillard), mais $C$ diverge vers 100 %. Le κ ajusté dépend du plafond : 0,126 (90 %), 0,066 (95 %), 0,044 (97 %), 0,021 (99 %), pour une erreur en log de 0,4847, 0,4855, 0,4885, 0,4969.
- **Pourquoi** : les hygromètres officiels ont ±2–3 % d'incertitude près de la saturation, et au-delà de ~95 % la loi de Köhler prédit une croissance que les données ne montrent pas.
- **Contournement** : HR plafonnée à `physics.rh_cap_pct` = 95 % (au lieu de l'ancienne règle « jamais au-dessus de 95 % » de l'étape 2, qui laissait la correction vide). L'humidité **saturée d'une sonde** reste, elle, manquante en amont. Test : à 99,99 % et 100 %, $C$ vaut $C(95\ \%)$, fini.
- **Impact** : les heures de brouillard gardent une correction physique ; le choix du plafond est documenté avec sa sensibilité.

### [2026-10-14] Échelle log : plancher de 1 µg/m³, et la correction de Duan n'est valable que pour un modèle à pente libre
- **Constat** : 12,4 % des heures du capteur et 0,6 % des heures de référence sont sous 1 µg/m³ dans la zone d'entraînement. Sur le modèle physique seul (pente fixée à 1 en log), la correction de Duan (facteur 1,50) **aggrave** la moyenne retransformée (9,4 réel ; 11,8 sans correction ; 17,7 avec). Sur un modèle log-linéaire à pente libre appris hors pli, la pente vaut **0,47** et Duan ramène la moyenne de 8,2 à 9,6 µg/m³ (référence 9,4).
- **Pourquoi** : sous 1 µg/m³ la référence est au niveau de son bruit ; Duan suppose des résidus indépendants des variables, faux quand la pente est imposée à 1. La pente de 0,47 traduit une forte « régression vers la moyenne » : quand le capteur lit haut, une partie de l'écart est du bruit.
- **Contournement** : `features.log_floor_ugm3 = 1.0` (`log_reference`, `log_pm25`, `log_physics_pm25`) ; `evaluation.metrics.duan_smearing` et `back_transform` prêts, testés (sur des données log-normales, la moyenne est retrouvée à 1 % près) ; à l'étape 6, le facteur sera calculé sur les résidus d'entraînement du modèle final, pli par pli, et vérifié sur les moyennes hors pli. Les métriques EPA restent en µg/m³.
- **Impact** : un modèle qui garde la pente 1 de la physique ne pourra pas corriger le bruit du capteur ; c'est un argument pour le modèle appris.

### [2026-10-14] 48 variables en 9 familles, sans référence ni futur
- **Constat** : la variable la plus liée à l'erreur à corriger ($\ln$ réf − $\ln$ brut) est l'**écart aux voisins citoyens** (Spearman −0,65), devant le niveau du capteur (−0,64), ses moyennes glissantes (≈ −0,6), l'humidité (−0,31) et $T - T_d$ (+0,31). La distance capteur–station, la distance à la station météo et les taux de manques n'y sont presque pas liés (|ρ| ≤ 0,02).
- **Pourquoi** : un capteur qui lit plus haut que ses voisins se trompe vers le haut ; le reste confirme le notebook 04.
- **Contournement** : familles documentées dans `schemas/gold.py` (`FEATURES`) et le notebook 05 ; temps en heure **locale** (Europe/Paris, commune à l'Allemagne et la France) encodé en sinus/cosinus ; point de rosée par la formule de Magnus ; fenêtres glissantes $(h-w, h]$ et taux de manques sur $[h-w, h)$, **jamais une heure future** : test `test_no_feature_uses_the_future` (on rend absurdes les valeurs après $t$, les variables jusqu'à $t$ ne bougent pas) et vérification sur une vraie paire de 17 177 heures dans le notebook. Voisins : autres capteurs à ≤ 3 km, au moins 3 valides à l'heure (79 % des heures d'entraînement). Les scores de santé, calculés sur toute la période, ne sont **pas** des variables horaires (ils regarderaient le futur).
- **Impact** : limite : les voisins sont seulement les capteurs téléchargés pour ce projet (le vrai réseau en compte davantage) ; la sonde locale n'est renseignée que pour 25 % des heures.

### [2026-10-14] Bonus : la santé d'un capteur se lit sur ses voisins, sans station
- **Constat** : sur 999 paires ayant assez d'heures avec au moins 3 voisins (156 invérifiables), la corrélation des variations horaires du capteur avec la médiane de ses voisins sépare les invérifiables des saines avec une AUC de **0,90** (0,91 pour les niveaux). Au seuil 0,1 (par analogie avec le contrôle d'horloge) : 69 paires signalées, 58 vraies invérifiables, **précision 84 %, rappel 37 %** ; au seuil 0,2 : précision 73 %, rappel 56 %.
- **Pourquoi** : un capteur en panne ne suit plus les variations de l'air que ses voisins, eux, suivent.
- **Contournement** : `transform/neighbours.health_scores`, `health.change_corr_min = 0.1` ; résultats par paire dans `gold/pairs.parquet`.
- **Impact** : **argument produit** : un réseau peut signaler ses capteurs suspects sans station officielle. Limites : 432 paires sans assez de voisins, seuil non validé sur des données séparées.

### [2026-10-14] Disque et mémoire : encodage delta, Float32, calcul des voisins mois par mois
- **Constat** : avec la couche gold (1,1 Go), `data/` atteignait 10,5 Go. Le premier calcul des voisins a été tué faute de mémoire (code 137) : la jointure heure × voisins fait ~10⁸ lignes, avec des identifiants texte.
- **Pourquoi** : l'horodatage représentait la moitié des mesures silver ; la jointure tenait entière en mémoire.
- **Contournement** : mesures silver écrites avec l'**encodage delta** de Parquet pour les colonnes triées (`sensor_id`, `segment`, `timestamp`), sans perte (relecture identique vérifiée) : 2,76 → 1,37 Go ; il n'aide pas la couche gold (essayé, +3 %), qui stocke ses nombres décimaux en Float32. Voisins : identifiants entiers et calcul mois par mois (pic mémoire 30 Go sur 48, 39 s pour toute l'étape).
- **Impact** : `data/` = **9,1 Go** (bronze 5,0, silver 2,7, gold 1,1), sous le plafond de 10 Go.

## Étape 6 — Modélisation et suivi MLflow

### [2026-10-14] LightGBM ne trouvait pas OpenMP sur macOS ; uv choisissait un vieux MLflow
- **Constat** : `import lightgbm` échouait (`Library not loaded: @rpath/libomp.dylib`), Homebrew n'étant pas installé. Par ailleurs, `uv` résolvait **MLflow 1.27** (erreur `protobuf.service` à l'import) à cause de la contrainte `pyarrow>=26` du projet.
- **Pourquoi** : la roue LightGBM pour macOS ne fournit pas OpenMP ; les MLflow récents imposent un pyarrow plus ancien que 26, donc le résolveur remontait jusqu'à une version de 2022.
- **Contournement** : `models/runtime.ensure_openmp()` relance le processus une seule fois avec `DYLD_FALLBACK_LIBRARY_PATH` pointant vers le `libomp.dylib` livré avec scikit-learn (aucune installation système). Contrainte relâchée à `pyarrow>=21`, ce qui donne MLflow 3.17.1 ; `requirements.txt` régénéré.
- **Impact** : `python -m aircalib train` fonctionne sur un Mac neuf sans Homebrew. Sous Linux, la fonction ne fait rien.

### [2026-10-14] La pente EPA d'un modèle de régression vaut à peu près son R²
- **Constat** : sur l'échelle de modèles, la pente EPA suit le $R^2$ : 0,48 pour 0,40 (linéaire en log), 0,81 pour 0,74 (LightGBM). Seul le modèle physique, qui n'est pas une régression, a une pente proche de 1 (1,11) avec un $R^2$ de 0,16.
- **Pourquoi** : un modèle qui minimise l'erreur quadratique est calibré ($\mathbb{E}[y \mid \hat y] = \hat y$), donc $\operatorname{cov}(\hat y, y) = \operatorname{var}(\hat y)$ et la pente de $\hat y$ sur $y$ vaut $\operatorname{var}(\hat y)/\operatorname{var}(y) = R^2$ (démonstration dans le notebook 06, section 1). Il resserre ses prédictions vers la moyenne.
- **Contournement** : pente et $R^2$ donnés côte à côte. Test d'une recalibration linéaire apprise hors pli (voir plus bas). Test `test_epa_slope_regresses_prediction_on_reference` pour la convention EPA ($\hat y$ régressé sur la référence).
- **Impact** : avec $R^2 \approx 0{,}75$, la pente est autour de 0,8, déjà dans la cible EPA [0,65 ; 1,35]. Ce qui bloque les paires restantes, c'est le $R^2$.

### [2026-10-14] Échelle de modèles : les échelons 1 et 2 ne battent pas le précédent sur tout
- **Constat** (5 plis par station, volume complet, paires saines, horaire) :
  - capteur brut : RMSE 11,0 µg/m³, 24,8 % de paires EPA ;
  - linéaire en log : RMSE 6,0, mais 11,8 % de paires EPA ;
  - physique : RMSE 19,5, 26,4 % de paires EPA ;
  - LightGBM : RMSE 3,95, $R^2$ 0,74, 76,7 % de paires EPA en horaire, 85,6 % en journalier.
- **Pourquoi** : le linéaire corrige le biais moyen mais resserre ses prédictions (pente 0,48), d'où moins de paires dans la cible de pente. La physique redresse le gain (pente 1,11) mais multiplie par $a \approx 2{,}4$ les pics parasites du capteur, d'où un RMSE qui explose.
- **Contournement** : aucun ; ces échelons servent de références. La physique reste une **variable** du modèle appris.
- **Impact** : le saut vient du modèle appris avec toutes les variables. Les échelons simples ne sont pas des produits.

### [2026-10-14] Modèle hybride rejeté : pas robuste aux capteurs abîmés
- **Constat** : LightGBM appris sur le résidu $\ln$ réf − $\ln$ physique fait jeu égal sur les paires saines (RMSE 4,00 contre 3,95 ; 78,0 % contre 76,7 % de paires EPA en horaire). Sur toutes paires, il s'effondre : RMSE 6,52 et $R^2$ 0,48, contre 4,30 et 0,70.
- **Pourquoi** : la prédiction part de la physique, qui amplifie les pics d'un capteur défectueux ; le résidu appris ne sait pas l'annuler sur des capteurs jamais vus à l'entraînement.
- **Contournement** : LightGBM direct retenu (`models.final.residual_of: ""`).
- **Impact** : le produit doit rester correct sur un capteur que personne n'a encore déclaré défectueux.

### [2026-10-14] L'intervalle 10–90 % des quantiles ne couvre que 62 %
- **Constat** : la régression quantile (10 %, 50 %, 90 %) a une médiane aussi bonne que LightGBM (RMSE 3,95), mais la référence n'est dans l'intervalle 10–90 % que 62 % du temps (attendu : 80 %), pour une largeur médiane de 4 µg/m³.
- **Pourquoi** : les quantiles sont appris sur les stations d'entraînement. Une station nouvelle a son propre biais, que l'intervalle ignore.
- **Contournement** : l'intervalle n'est pas livré tel quel. Piste pour l'étape 7 : calibration conforme par station (élargir l'intervalle avec les résidus hors pli).
- **Impact** : pas d'incertitude annoncée au client à ce stade (limite).

### [2026-10-14] Cible en µg/m³ plutôt qu'en log
- **Constat** : même LightGBM, mêmes plis, sous-échantillon : en µg/m³, RMSE 4,22 et 72,6 % / 84,4 % de paires EPA (horaire / journalier) ; en log avec la correction de Duan pli par pli, RMSE 4,30 et 70,5 % / 82,2 %. La pente est identique (0,79).
- **Pourquoi** : le log optimise une erreur relative, alors que les cibles EPA sont absolues ; sur ce sous-échantillon, une paire vaut 1,05 point de « % EPA ».
- **Contournement** : `models.final.log_target: false`. Duan reste dans le code pour le linéaire en log et l'échelon 3.
- **Impact** : voir l'entrée « point de vigilance » : sur le volume complet, ce choix n'est pas gagnant pour les capteurs abîmés.

### [2026-10-14] Contrainte monotone gardée, perte nulle
- **Constat** : RMSE 4,29 avec la contrainte contre 4,30 sans ; paires EPA identiques en horaire (70,5 %), 83,3 % contre 82,2 % en journalier.
- **Pourquoi** : la contrainte (méthode `advanced` de LightGBM, sur le PM brut, son log et la physique) n'interdit que des creux non physiques.
- **Contournement** : `models.final.monotone: true`, variables dans `models.monotone_features`.
- **Impact** : garantie défendable devant un client : à conditions égales, une lecture brute plus haute ne fait jamais baisser la valeur corrigée.

### [2026-10-14] Recalibration de la pente rejetée
- **Constat** : la recalibration apprise hors pli ($\hat y' = (\hat y - b)/s$, $s$ et $b$ estimés par une validation croisée interne sur les plis d'entraînement) amène la pente à 1,01. Mais le RMSE passe de 4,30 à 5,12 et les paires EPA journalières de 82,2 % à 75,6 %.
- **Pourquoi** : « déresserrer » les prédictions amplifie aussi leur erreur ; la pente n'était pas ce qui bloquait.
- **Contournement** : `models.final.recalibrate: false`.
- **Impact** : le compromis prédiction / étalonnage est documenté (notebook 06, section 8).

### [2026-10-14] Sonde locale retirée du modèle
- **Constat** : ablation sur le sous-échantillon : sans la famille « sonde locale (secondaire) », le RMSE passe de 4,30 à 4,27 et les paires EPA de 70,5 % à 71,6 % en horaire, de 82,2 % à 84,4 % en journalier.
- **Pourquoi** : l'humidité officielle porte déjà l'information ; la sonde n'est fiable que 25 % des heures (notebook 02) et son humidité lit trop sec.
- **Contournement** : `models.final.drop_families: ["sonde locale (secondaire)"]` ; le modèle final a 45 variables. Le seuil X de la sonde n'a plus d'effet (sensibilité X = 0,1 / 0,3 / 0,5 : RMSE 4,32 / 4,30 / 4,29, négligeable).
- **Impact** : **argument produit** : la correction n'a besoin ni d'une sonde d'humidité ni d'une sonde qui marche.

### [2026-10-14] Le voisinage citoyen est la famille décisive ; les valeurs manquantes sont bien gérées
- **Constat** : sans le voisinage, le RMSE passe de 4,30 à 5,19 (+21 %) et les paires EPA de 70,5 % à 54,7 % en horaire. Viennent ensuite :
  - dynamique passée : +4 % ;
  - variables de temps : +2,5 % ;
  - météo officielle : +1,3 % ;
  - capteur brut : +1,1 %.

  Sur les heures **sans voisins**, le modèle complet (qui voit des voisins « manquants ») fait 5,77 contre 5,67 pour un modèle appris sans la famille. La médiane des voisins porte 38 % du gain du modèle final.
- **Pourquoi** : les voisins mesurent le même air au même moment, avec le même type de capteur. LightGBM apprend de quel côté envoyer une valeur manquante, mais le modèle a été optimisé surtout pour les heures avec voisins.
- **Contournement** : la gestion native des manquants est gardée (≈ 2 % de perte sur ces heures seulement). Un modèle dédié aux heures sans voisins est une piste pour l'étape 7. Limite de la méthode : retirer une famille à la fois sous-estime les familles redondantes (le brut et la physique portent la même information).
- **Impact** : voir l'entrée suivante.

### [2026-10-14] Limite principale du produit : les heures sans voisins citoyens
- **Constat** (modèle retenu, 5 plis, volume complet) : 21 % des heures (20,8 % pour les paires saines) n'ont pas au moins 3 voisins valides à ≤ 3 km.

  | | RMSE horaire | % paires EPA horaire | % paires EPA journalier |
  |---|---|---|---|
  | Paires saines, avec voisins | 3,39 µg/m³ | 88,3 % | 92,0 % |
  | Paires saines, sans voisins | 5,21 µg/m³ | 42,1 % | 58,6 % |
  | Toutes paires, avec voisins | 3,54 µg/m³ | 84,3 % | 90,2 % |
  | Toutes paires, sans voisins | 6,59 µg/m³ | 36,4 % | 49,6 % |

  Toutes heures confondues, le RMSE vaut 3,84 (saines) et 4,38 (toutes). Sur le sous-échantillon, le RMSE sans voisins était de 5,7 pour 4,3 en moyenne.
- **Pourquoi** : sans voisins, le modèle ne peut corriger que le biais moyen du capteur (humidité, gain), pas ses écarts propres à chaque heure.
- **Contournement** : le découpage « avec / sans voisins » est donné dans tous les tableaux finaux, y compris le % de paires EPA (calculé sur les heures de chaque type, paires ayant ≥ 500 heures ou ≥ 30 jours de ce type). Pire cas : un capteur invérifiable sans voisins (RMSE 10,7, $R^2$ 0,08).
- **Impact** : la promesse du produit dépend de la **densité du réseau**. Un capteur isolé n'atteint pas les cibles EPA dans plus de la moitié des cas.

### [2026-10-14] Le réglage des hyperparamètres n'apporte que ≈ 2 % ; Optuna arrêté à 15 essais
- **Constat** : 15 essais Optuna (TPE, graine 42) au lieu des 50 prévus. Le meilleur essai (n° 14 : 453 feuilles, 59 lignes par feuille, pas 0,023, 1 084 itérations) donne un RMSE de 4,22 contre 4,30 par défaut (−1,9 %). Il gagne sur 4 plis et fait jeu égal sur le 5ᵉ. Sur le volume complet : 3,84 contre 3,91 (−1,8 %), 79,7 % contre 77,3 % de paires EPA en horaire, identique en journalier (85,3 %). En comparaison, retirer le voisinage coûte +21 % et passer du linéaire à LightGBM avec toutes les variables gagne 35 %.
- **Pourquoi** : les essais s'allongeaient (jusqu'à 4,6 min par essai sur le sous-échantillon) pour un gain marginal. **La performance vient des données et des variables**, pas du réglage fin.
- **Contournement** : recherche arrêtée, meilleurs réglages gardés (`data/models/optuna_best.json`, `models.final.use_optuna: true`). `models.optuna_trials` vaut 15 et l'espace de recherche est dans la configuration (`models.optuna_space`), pour que la recherche soit reproductible.
- **Impact** : le modèle final avec les réglages Optuna entraîne en 7,5 min sur 5 plis, contre 2,6 min par défaut, pour 2 % de gain.

### [2026-10-14] Optuna contre ablation : même protocole, un écart de lecture
- **Constat** : un RMSE d'environ 5,7 lu dans l'ablation semblait contredire les 4,22 d'Optuna.
- **Pourquoi** : vérification faite :
  - même découpage par station (colonne `fold` de la couche gold, GroupKFold sur `cv_group`) ;
  - même sous-échantillon (20 % des paires, graine 42 : 1 169 171 heures de test des deux côtés) ;
  - même empreinte de la table gold et même métrique (RMSE horaire, paires saines, µg/m³ après retour à l'échelle).
  
  Les 5,7 étaient le RMSE sur les **heures sans voisins**, pas le RMSE global (4,30).
- **Contournement** : les tableaux d'ablation affichent désormais les deux colonnes (avec / sans voisins).
- **Impact** : aucun sur les résultats ; c'est ce chiffre qui a fait apparaître la limite principale du produit.

### [2026-10-14] Léger optimisme du chiffre final : hyperparamètres choisis sur les mêmes plis
- **Constat** : les réglages Optuna ont été choisis sur les 5 plis du sous-échantillon. Les stations de test du modèle final sont donc celles qui ont servi au choix.
- **Pourquoi** : une validation imbriquée (relancer Optuna sans le pli de test, pour chacun des 5 plis) aurait coûté ≈ 3 h de calcul pour un réglage qui rapporte 2 %.
- **Contournement** : non faite. Le modèle final est donné avec les réglages par défaut (choisis avant de voir les données) **et** avec les réglages Optuna. L'optimisme est borné par leur écart : ≤ 0,07 µg/m³ de RMSE, ≤ 2,4 points de % EPA horaire.
- **Impact** : le chiffre de référence de l'étape (3,84 µg/m³ ; 79,7 % / 85,3 %) est légèrement optimiste. Le chiffre « par défaut » (3,91 ; 77,3 % / 85,3 %) ne l'est pas. Le test 2024 → 2025 et le transfert vers la France (étape 7) seront, eux, hors sélection.

### [2026-10-14] Point de vigilance : sur toutes paires, le modèle retenu fait moins bien que l'échelon 3
- **Constat** : par rapport à l'échelon 3 (cible log, sans contrainte, avec sonde, réglages par défaut), le modèle final par défaut (µg/m³, contrainte, sans sonde) :
  - gagne sur les paires saines : RMSE 3,91 contre 3,95, 77,3 % contre 76,7 % de paires EPA ;
  - perd sur toutes paires : RMSE 4,45 contre 4,30 ; sans voisins, 6,66 contre 6,18. Le % de paires EPA de toutes paires est pourtant égal (72,9 % contre 72,5 %).
  
  Sur le sous-échantillon, ces choix ne dégradaient pas toutes paires (4,31 contre 4,34).
- **Pourquoi** : non tranché ; les trois changements ont été faits ensemble. Hypothèse : une erreur quadratique en µg/m³ suit davantage les pics des capteurs abîmés qu'une erreur en log. Le sous-échantillon (95 paires saines notées) contenait trop peu de capteurs abîmés pour le voir.
- **Contournement** : choix gardé, car le produit vise les capteurs sains (le score de santé repère les autres). Le RMSE de toutes paires est surveillé à l'étape 7.
- **Impact** : une décision prise sur un sous-échantillon peut ne pas tenir sur le volume complet ; la décision log / µg pour les capteurs abîmés reste ouverte.

### [2026-10-14] Paires invérifiables à l'entraînement : un léger gain, décision gardée
- **Constat** : entraîner aussi sur les paires invérifiables donne, sur le sous-échantillon, un RMSE de 4,28 contre 4,30 (paires saines) et 4,31 contre 4,34 (toutes paires), et 73,7 % contre 70,5 % de paires EPA en horaire (3 paires).
- **Pourquoi** : le modèle verrait des capteurs abîmés et apprendrait peut-être à s'en méfier ; l'écart reste dans le bruit du sous-échantillon.
- **Contournement** : décision de l'étape 5 maintenue : on n'apprend pas sur des paires non vérifiées.
- **Impact** : proposition pour l'étape 7, à mesurer sur le volume complet.

### [2026-10-14] Score de santé : seuil validé sur un pli tenu à l'écart, transfert faible vers la France
- **Constat** : pour chaque pli, on choisit le seuil maximisant le F1 sur les 4 autres plis ; il vaut entre 0,17 et 0,29. Sur le pli tenu à l'écart :
  - précision de 48 % à 83 % (65 % au total, 81 sur 125) ;
  - rappel de 48 % à 69 % (56 % au total, 81 sur 144).

  En France (50 paires notées, 12 invérifiables), le seuil 0,19 choisi sur l'Allemagne signale 6 paires dont 3 justes : précision 50 %, rappel 25 %. Le seuil fixe 0,1 donne 85 % de précision et 40 % de rappel en Allemagne, mais ne repère qu'une paire abîmée sur 12 en France.
- **Pourquoi** : en Allemagne, le score est solide. En France, l'échantillon est petit et le voisinage peut-être plus clairsemé (hypothèse).
- **Contournement** : `evaluation/health.threshold_cv`, testé (le seuil n'est jamais choisi avec le pli de test). Le seuil 0,1 est gardé comme réglage prudent (peu de fausses alertes).
- **Impact** : l'argument produit « repérer les capteurs en panne sans station » tient en Allemagne (≈ 2 alertes sur 3 justes), pas encore en France. À revoir à l'étape 7.

### [2026-10-14] Temps de calcul et disque de l'étape 6
- **Constat** : sur MacBook, entraînement sur 5 plis :
  - échelle complète : 15 min, dont 8,6 min pour les quantiles ;
  - ablation (10 runs, sous-échantillon) : 11 min ;
  - Optuna (15 essais) : 34 min ;
  - modèle final : 2,6 min avec les réglages par défaut, 7,5 min avec ceux d'Optuna.

  La prédiction prend de 3 à 13 s par pli. MLflow occupe 0,5 Go (`mlruns/` : prédictions hors pli en Parquet, une par run) ; `mlflow.db` fait 4 Mo.
- **Pourquoi** : les prédictions hors pli sont gardées pour pouvoir recalculer les métriques sans réentraîner (`python -m aircalib train --study rescore` a ajouté le découpage avec / sans voisins aux runs de l'échelle).
- **Contournement** : les essais Optuna ne gardent pas leurs prédictions.
- **Impact** : `du` donne 8,7 Gio pour `data/` et 0,5 Gio pour `mlruns/`, soit ≈ 9,2 Gio, sous le plafond de 10 Go.

### [2026-10-15] Recul « toutes paires » : la cible en µg/m³ en est la cause ; modèle retenu gardé
- **Constat** : on part de l'échelon 3 (cible log, sans contrainte, avec la sonde locale) et on applique chaque changement seul, puis les trois ensemble. Volume complet, 5 plis, réglages par défaut (`python -m aircalib train --study attribution`).

  | Variante | RMSE saines | % EPA saines | RMSE toutes | % EPA toutes | RMSE toutes, sans voisins | % EPA toutes, sans voisins |
  |---|---|---|---|---|---|---|
  | Échelon 3 | 3,95 | 76,7 | 4,30 | 72,5 | 6,18 | 38,3 |
  | + cible µg/m³ | 3,92 | 78,0 | **4,46** | 73,6 | **6,67** | 36,4 |
  | + contrainte monotone | 3,95 | 76,5 | 4,31 | 71,8 | 6,20 | 38,3 |
  | − sonde locale | 3,97 | 76,5 | 4,33 | 72,1 | 6,23 | 38,9 |
  | Les trois | 3,91 | 77,3 | 4,45 | 72,9 | 6,66 | 38,3 |

  RMSE horaire en µg/m³, % de paires passant les cibles EPA en horaire. L'échelon 3 rejoue exactement le run de l'étape 6 (calcul déterministe).
- **Pourquoi** : le recul vient de la **cible en µg/m³**. L'erreur quadratique en µg/m³ donne un poids énorme aux pics des capteurs abîmés et des heures sans voisins, alors que le log les écrase. La contrainte monotone ne change rien. Le retrait de la sonde locale coûte 0,02 µg/m³, ce qui reste dans le bruit ; sur le sous-échantillon, il faisait gagner 0,03.
- **Contournement** : la règle de décision fixée au départ était : on annule un changement qui cause le recul sans rien apporter sur les paires saines. La cible en µg/m³ apporte quelque chose sur les saines : −0,03 µg/m³ et +1,3 point de % EPA horaire (−0,3 point en journalier). **Le modèle retenu est donc gardé.** Justification : le produit vise les capteurs sains ; les capteurs abîmés sont repérés par le score de santé. Limite de cette justification : le score ne repère qu'environ 56 % des capteurs abîmés (notebook 06). La sonde locale reste retirée pour l'argument produit (aucune sonde d'humidité requise), à un coût de 0,02 µg/m³.
- **Impact** : la question est tranchée. Le modèle retenu est meilleur sur les capteurs sains et moins robuste sur les capteurs abîmés (+0,15 µg/m³ sur toutes paires). L'étape 7 continue de donner les deux chiffres.

## Étape 7 — Évaluation finale : temps, distance, France

### [2026-10-15] Test temporel : un an d'écart coûte 15 points de paires EPA ; il faut réapprendre chaque année
- **Constat** : modèle retenu, paires saines (RMSE horaire en µg/m³ ; % de paires passant les cibles EPA, horaire / journalier) :

  | Scénario | RMSE | % EPA |
  |---|---|---|
  | Référence (5 plis, deux années mélangées, heures 2025) | 3,91 | 82,4 / 86,9 |
  | (b0) nouvelles stations, appris et testé sur 2025 | 3,83 | 84,3 / 88,9 |
  | (b) nouvelles stations, 2024 → 2025 | **4,38** | **69,5 / 81,2** |
  | (a) mêmes stations, 2024 → 2025 | 3,69 | 82,0 / **92,1** |

  Pour (a), la référence sur les mêmes paires donne 3,93 et 82,7 / 87,1. Coût de l'année d'écart (b − b0) : +0,55 µg/m³ et −15 points en horaire. La pente tombe de 0,85 à 0,72. Mois par mois, le modèle de 2024 surestime les mois propres de 2025 (juin +1,45 µg/m³) et sous-estime les mois pollués (février −1,7), alors que le témoin garde un biais stable (+0,1 à +0,4). Par âge du capteur, le coût vaut +0,11 µg/m³ sous 1 an et +0,5 à +0,7 au-delà.
- **Pourquoi** : la relation capteur → référence change d'une année à l'autre (épisodes, composition de l'aérosol). Avec une seule année, le modèle n'en voit qu'une partie et comprime les valeurs nouvelles. L'âge joue peut-être (encrassement de l'optique), mais l'effet n'est pas régulier : c'est une hypothèse. Le volume d'apprentissage n'est pas en cause : (b0), avec un an, fait aussi bien que la référence avec deux.
- **Contournement** : aucun dans le modèle. Le témoin (b0) a les mêmes heures de test que (b) et un volume d'apprentissage comparable, ce qui isole le seul effet du temps. Le modèle physique ($a$, $\kappa$) est réajusté sur les seules lignes d'entraînement de chaque scénario (`models.data.fit_physics`).
- **Impact** : **recommandation produit : réapprendre le modèle chaque année** avec les données validées des stations, ou avec plusieurs années d'historique. Un client qui garde ses stations gagne surtout en journalier (92 %) et sur les heures sans voisins (60 % contre 52 %).

### [2026-10-15] Test distance : jusqu'à 3 km, la distance compte peu ; ce qui compte, ce sont les voisins
- **Constat** : protocole du plan (stations du pli exclues de l'entraînement, toutes leurs paires prédites jusqu'à 3 km). Paires saines, % de paires passant les cibles EPA (horaire / journalier) :

  | Distance | Corrigé | Brut |
  |---|---|---|
  | < 0,5 km | 81,7 / 88,3 | 27,0 / 36,9 |
  | 0,5–1 km | 80,2 / 86,1 | 26,5 / 38,6 |
  | 1–1,5 km | 78,0 / 82,8 | 22,0 / 40,8 |
  | 1,5–2 km | 80,4 / 88,1 | 23,1 / 40,4 |
  | 2–3 km | **70,1 / 80,1** | 22,6 / 35,5 |

  Avec voisins, de 81 à 92 % des paires passent en horaire à toutes les distances (2–3 km : 81,2 / 91,3). Sans voisins, de 40 à 50 %. Les heures sans voisins font 15 % sous 0,5 km, 21 à 25 % entre 0,5 et 2 km, et 38 % au-delà de 2 km. Toutes paires : 83,2 % sous 0,5 km et 64,0 % à 2–3 km.
- **Pourquoi** : le PM2.5 est un polluant surtout régional, donc l'air de fond est le même à quelques kilomètres, et la médiane des voisins ramène la correction au niveau local. Les capteurs loin des stations sont dans des zones moins denses, donc moins entourés de capteurs citoyens.
- **Contournement** : aucun. Limites :
  - l'« erreur » à distance mêle l'erreur du capteur et la différence réelle d'air entre les deux sites ;
  - `distance_km` n'a été apprise que jusqu'à 1,5 km (au-delà, les arbres la traitent comme la plus grande valeur vue) ;
  - rien n'a été mesuré au-delà de 3 km.
- **Impact** : **réponse business : au moins 3 km, à condition d'avoir des voisins citoyens.**

### [2026-10-15] Transfert vers la France : échec tel quel, surtout faute de voisins
- **Constat** : modèle réentraîné sur toutes les paires saines allemandes ≤ 1,5 km, appliqué sans réajustement (physique comprise). Paires saines, % de paires passant les cibles EPA (horaire / journalier) :
  - ensemble : **22,8 / 35,2** après correction, contre 15,8 / 24,2 brutes ; RMSE 7,88 → 5,47 µg/m³, $R^2$ 0,35 → 0,55 ;
  - toutes paires : 18,4 / 28,5, contre 11,8 / 17,9 brutes (le même « avant » qu'au notebook 04) ;
  - avec voisins : 50,0 / 52,9 ; sans voisins : 12,7 / 24,2 ;
  - bande 1,5–3 km avec voisins : 62,1 % en horaire, $R^2$ 0,72 ;
  - par méthode de la station : optique 46,7 %, TEOM 5,9 % (11,8 % brut), bêta 0 %.

  Le niveau moyen est juste (prédiction 9,7 µg/m³ pour une référence de 9,4) ; c'est la pente qui manque (0,54).
- **Pourquoi** : 61 % des heures françaises des paires saines n'ont pas de voisins (68 % ≤ 1,5 km), contre 21 % en Allemagne. Les références françaises sont plus souvent des TEOM et des jauges bêta, au temps de réponse différent, et la météo officielle vient d'un autre réseau.
- **Contournement** : aucun réajustement, comme demandé.
- **Impact** : le modèle ne se vend pas tel quel en France. Il faudrait une calibration locale (quelques stations françaises à l'entraînement) et un réseau plus dense. Avec voisins, l'écart à l'Allemagne se réduit nettement.

### [2026-10-15] Découpages : les stations industrielles, le TEOM en horaire et le brouillard sont les points faibles
- **Constat** : modèle retenu, Allemagne ≤ 1,5 km, paires saines, % de paires passant les cibles EPA (horaire / journalier) :
  - type de station : trafic 82,4 / 88,6, fond 79,4 / 82,4, **industriel 0 / 33** (8 paires) ;
  - méthode de mesure : optique 85,1 / 87,4, bêta 81,0 / 90,5, néphélométrie + bêta 57,4 / 70,0, TEOM 54,5 / 90,9 ;
  - saison : hiver 80,2, printemps 75,8, automne 72,4, été 69,0 en horaire (RMSE estival 2,70 µg/m³ mais $R^2$ 0,60) ;
  - humidité (horaire) : 70,8 sous 50 %, 80 à 82 entre 60 et 95 %, **69,4 au-delà de 95 %**.
- **Pourquoi** :
  - le TEOM répond avec une demi-heure de retard : cela pèse en horaire et disparaît en journalier ;
  - en été, la référence varie peu, ce qui fait baisser mécaniquement le $R^2$, donc la pente ;
  - au-delà de 95 %, l'humidité est plafonnée dans le modèle physique (brouillard) ;
  - près des sites industriels, les sources sont locales et il n'y a presque pas de voisins.
- **Contournement** : aucun. Une tranche (saison, humidité) a toujours un % EPA plus bas que l'ensemble, parce que la référence y varie moins.
- **Impact** : à annoncer au client. Le produit tient sa promesse en trafic et en fond, pas près d'un site industriel.

### [2026-10-15] Intervalle de prédiction : la calibration conforme ramène la couverture à 80 %
- **Constat** : l'intervalle 10–90 % de la régression quantile couvrait 62,4 % (paires saines). Après calibration conforme, pli par pli, il couvre :
  - **79,8 %** avec une seule calibration (77,7 à 82,2 % selon le pli), mais 75,4 % seulement sur les heures sans voisins ;
  - 79,7 % avec voisins et 79,6 % sans, avec une calibration par voisinage.

  La largeur médiane passe de 4,0 à 6,0 µg/m³ : 5,6 avec voisins et 7,8 sans dans la variante par voisinage.
- **Pourquoi** : les quantiles appris ignorent le biais propre à une station nouvelle. Sans voisins, l'incertitude est plus grande.
- **Contournement** : `evaluation/conformal.py`, testé (la couverture est restaurée sur des plis jamais utilisés pour la calibration ; un pli ne se calibre jamais lui-même). L'écart de chaque heure est mesuré en log, parce que l'erreur est multiplicative. Variante retenue : une calibration par voisinage. Limites :
  - les écarts des 4 plis de calibration viennent de modèles qui avaient appris le 5ᵉ, donc l'échangeabilité n'est qu'approchée ;
  - l'intervalle est celui du modèle quantile, pas du modèle retenu.
- **Impact** : un intervalle annoncé « 80 % » tient sa promesse sur des stations jamais vues ; il est plus large quand le capteur est isolé.

### [2026-10-15] Comparaison à la littérature : un R² comparable à Oslo, sous un protocole plus dur
- **Constat** : Liu et al. (2019, *Atmosphere* 10(2), 41) ont testé trois SDS011 sur le site d'une station TEOM d'Oslo pendant 4 mois. Leur $R^2$ va de 0,55–0,71 avant correction à 0,76–0,80 après (chiffres du résumé de l'article). Nous obtenons 0,18 → 0,76 sur les paires saines, et 0,81 avec voisins, sur des stations jamais vues ; la France tombe à 0,55.
- **Pourquoi** :
  - notre $R^2$ est regroupé sur 459 paires et deux ans (les écarts de niveau entre sites y pèsent) ;
  - le modèle n'a jamais vu ni le capteur testé ni sa station ;
  - nous utilisons les capteurs voisins, une information que l'étude d'Oslo n'avait pas.
- **Contournement** : la différence de protocole est écrite à côté des chiffres (notebook 07, section 6).
- **Impact** : le résultat allemand est au niveau de la littérature, avec un protocole plus exigeant.

### [2026-10-15] Disque : prédictions des runs d'attribution supprimées
- **Constat** : avec les runs de l'étape 7, `mlruns/` atteignait 0,95 Gio, et le total ≈ 9,65 Gio, soit 10,4 Go.
- **Pourquoi** : chaque run garde ses prédictions hors pli (≈ 46 Mo pour 5,5 M d'heures, 110 Mo pour le test distance).
- **Contournement** : les 5 fichiers de prédictions des runs d'attribution ont été supprimés (232 Mo) ; leurs métriques et leurs tables restent dans MLflow. L'étude ne les enregistre plus. Les « capteurs bruts » de l'étape 7 n'enregistrent que leurs tables par tranche.
- **Impact** : `data/` fait 8,7 Gio et `mlruns/` 0,7 Gio, soit ≈ 9,4 Gio.
