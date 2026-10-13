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
