# Journal de bord

Les problèmes rencontrés et les décisions prises, dans l'ordre. Tous les chiffres viennent de cette branche.

**1. Périmètre.** Allemagne, capteurs SDS011 à 1,5 km ou moins d'une station EEA mesurant le PM2.5 en automatique,
2024–2025. Trois sources seulement (Sensor.Community, EEA, DWD), quatre scripts, un petit paquet `aircalib`.

**2. En-tête HTTP refusé.** Notre User-Agent contenait un accent : httpx refusait de l'encoder. On l'a écrit en ASCII.

**3. Stations sans données.** Dans le mode échantillon, 5 stations sur 10 n'avaient aucun fichier E1a sur la période.
Décision : télécharger la référence **avant** d'apparier, et ne garder que les stations avec au moins 500 heures
valides et vérifiées.

**4. Trop peu de paires.** Le premier appariement ne prenait que le point de mesure EEA le plus proche, parfois un
ancien identifiant sans données : 419 paires. En téléchargeant tous les points à 1,5 km ou moins d'un capteur, puis en
appariant parmi ceux qui ont des données : **569 paires, 180 stations**.

**5. L'horloge de l'EEA.** Sur les 37 paires les plus proches (moins de 300 m), la corrélation des variations horaires
est maximale avec un décalage d'une heure pour 35 d'entre elles (0,43 contre 0,10 sans décalage). On corrige :
heure UTC = Start − 1 h (notebook 01).

**6. Sondes d'humidité inutilisables.** En janvier 2025, 68 % des mesures des DHT22 et 51 % de celles des BME280 sont
bloquées à 99,9–100 %, contre 3,3 % pour les stations météo officielles. On prend l'humidité du DWD (notebook 01).

**7. Cadence des capteurs.** 81 % des capteurs envoient une mesure toutes les 2,4–2,5 min, mais certains toutes les
minutes ou toutes les 5 minutes. La complétude d'une heure est jugée par rapport à la cadence propre du capteur
(75 % des mesures attendues), pas avec un nombre fixe (notebook 02).

**8. LightGBM et OpenMP sur macOS.** `uv run` ne transmet pas les variables `DYLD_*`. Les scripts se relancent
eux-mêmes en pointant vers la copie d'OpenMP livrée avec scikit-learn ; `make notebooks` passe la variable
directement. Dans VS Code, il faut `brew install libomp`.

**9. Le mode échantillon écrasait nos résultats.** Ses sorties (journal, entonnoir) allaient dans `reports/`. Elles
vont maintenant dans `data/echantillon/`.

**10. La régression linéaire à 0 % EPA.** Ce n'est pas un bug. Pour un modèle des moindres carrés, la pente EPA vaut à
peu près son R², et quelques valeurs brutes extrêmes (jusqu'à 990 µg/m³) écrasent ce R² et le coefficient du
PM2.5 (notebook 03).

**11. Premier essai complet : les heures sans voisins.** Avec les seuls capteurs appariés comme voisins, 60 % des
heures avaient une médiane des voisins. LightGBM passait les cibles EPA pour 64,2 % des paires, mais 79 % sur les
heures avec voisins contre 37 % sans. On a téléchargé en plus les 808 capteurs SDS011 non appariés à moins de 3 km
d'un capteur apparié (environ 1 h 30 de plus) : 79 % des heures ont maintenant des voisins
(`reports/resultats_premier_essai.csv`).

**12. Plus de variable sur la station.** Le type de station et la distance capteur–station n'existent pas pour un
capteur loin de toute station, qui est justement l'usage visé. Sur l'échantillon, les retirer ne coûte rien de
significatif (3 paires sur 60, avec un RMSE meilleur sans elles). On les garde seulement pour découper les résultats.

**13. Le modèle combiné.** L'ablation montre que, sur les heures sans voisins, le modèle appris sans les variables de
voisins fait mieux. Le modèle retenu prend le LightGBM complet quand le capteur a des voisins, l'autre sinon :
**75,7 % des paires EPA en horaire, 86,5 % en journalier** sur toutes les stations (notebooks 03 et 04).

**14. Contre-vérification des métriques.** Tous nos chiffres sortent d'une seule fonction (`resume`). Le notebook 05
les recalcule sans `src/`, avec des sommes en numpy, à partir des prédictions sauvegardées, et vérifie qu'ils
concordent avec le journal des essais à l'arrondi près.

**15. `make notebooks` et les chemins avec une espace.** Le chemin d'OpenMP passé à LightGBM n'était pas entre
guillemets : la commande échouait dans un dossier comme « Application Support ». Corrigé dans le `Makefile`.

**16. Robustesse de l'évaluation.** Le notebook 06 met nos chiffres à l'épreuve. Un bootstrap par station donne un
intervalle à 95 % d'environ 69–82 % de paires EPA ; le combiné et le réglage 1 sont indistinguables. Des plis par
région (stations à 10 km ou moins regroupées) coûtent environ 3 à 4 points. Mais appris sur une année et testé sur
l'autre, le modèle perd de 10 à 23 points : il faut le réapprendre régulièrement. C'est la limite la plus
importante qu'on ait trouvée.
