# Rapport d'entonnoir — mode échantillon (`--sample`)

_Généré automatiquement le 2026-10-14 12:14 UTC à partir des statuts de la couche silver (aucune ligne supprimée : chaque mesure ou heure garde sa raison)._

## 1. Mesures SDS011 (une ligne = une mesure brute)

| step | raison | removed | remaining | capteurs_lost | capteurs_remaining | segments_lost | segments_remaining |
|---|---|---|---|---|---|---|---|
| start | point de départ | 0 | 328,671 | 0 | 20 | 0 | 20 |
| pm_missing | PM10 ou PM2.5 vide / NaN | 0 | 328,671 | 0 | 20 | 0 | 20 |
| duplicate | doublon exact (même horodatage, mêmes valeurs) : la 1re copie est gardée | 0 | 328,671 | 0 | 20 | 0 | 20 |
| duplicate_conflict | même horodatage, valeurs différentes : impossible de choisir | 0 | 328,671 | 0 | 20 | 0 | 20 |
| pm_negative | valeur négative (code d'erreur −1 du firmware) | 0 | 328,671 | 0 | 20 | 0 | 20 |
| pm_saturated | saturation du capteur (PM2.5 = 999,9 avec PM10 = 1 999,9) | 4 | 328,667 | 0 | 20 | 0 | 20 |
| pm_above_range | au-delà de la plage du SDS011 (999,9 µg/m³) | 3 | 328,664 | 0 | 20 | 0 | 20 |
| pm25_above_pm10 | PM2.5 > PM10 : incohérent (PM2.5 fait partie de PM10) | 0 | 328,664 | 0 | 20 | 0 | 20 |
| pm_stuck | valeurs identiques pendant ≥ 1 h (`particles.stuck_min_hours`) : capteur figé | 764 | 327,900 | 0 | 20 | 0 | 20 |
| usable | **utilisable** | 0 | 327,900 | 0 | 20 | 0 | 20 |

## 2. Paires × heures (une ligne = une heure d'un segment de capteur)

| step | raison | removed | remaining | capteurs_lost | capteurs_remaining | paires_lost | paires_remaining | stations_lost | stations_remaining |
|---|---|---|---|---|---|---|---|---|---|
| start | point de départ | 0 | 14,233 | 0 | 20 | 0 | 20 | 0 | 18 |
| no_valid_reading | aucune mesure valide dans l'heure (raison dominante dans `main_flag`) | 10 | 14,223 | 0 | 20 | 0 | 20 | 0 | 18 |
| incomplete_hour | complétude < 75 % de la cadence propre du capteur | 479 | 13,744 | 0 | 20 | 0 | 20 | 0 | 18 |
| no_reference_station | segment de position sans station utilisable à ≤ 3 km | 0 | 13,744 | 0 | 20 | 0 | 20 | 0 | 18 |
| reference_not_downloaded | station de référence non téléchargée | 0 | 13,744 | 0 | 20 | 0 | 20 | 0 | 18 |
| reference_missing | pas de valeur EEA PM2.5 à cette heure | 0 | 13,744 | 0 | 20 | 0 | 20 | 0 | 18 |
| reference_invalid | valeur EEA PM2.5 invalide (drapeau ou −999) | 94 | 13,650 | 0 | 20 | 0 | 20 | 0 | 18 |
| reference_not_verified | valeur EEA PM2.5 non vérifiée | 0 | 13,650 | 0 | 20 | 0 | 20 | 0 | 18 |
| reference_negative_beyond_uncertainty | valeur EEA PM2.5 < −5 µg/m³ | 0 | 13,650 | 0 | 20 | 0 | 20 | 0 | 18 |
| clock_mismatch_station | station exclue : son décalage d'horloge (toutes paires) ≠ 0 | 0 | 13,650 | 0 | 20 | 0 | 20 | 0 | 18 |
| clock_mismatch | paire exclue : son meilleur décalage d'horloge ≠ 0 | 0 | 13,650 | 0 | 20 | 0 | 20 | 0 | 18 |
| no_official_humidity | aucune humidité officielle (ni station principale, ni secours) | 0 | 13,650 | 0 | 20 | 0 | 20 | 0 | 18 |
| usable | **utilisable** | 0 | 13,650 | 0 | 20 | 0 | 20 | 0 | 18 |

### Heures-paires utilisables par pays et bande de distance

| country | distance_band | heures_paires | paires | capteurs | stations | pct_humidité_locale_fiable | pct_secours_météo |
|---|---|---|---|---|---|---|---|
| DE | far | 3,248 | 5 | 5 | 5 | 11.5 | 0.060 |
| DE | near | 3,550 | 5 | 5 | 5 | 20.2 | 0.650 |
| FR | far | 4,108 | 6 | 6 | 4 | 15.6 | 0.000 |
| FR | near | 2,744 | 4 | 4 | 4 | 74.6 | 0.000 |

### Heures-paires utilisables par pays et type de station

| country | station_type | heures_paires | paires | capteurs | stations | pct_humidité_locale_fiable | pct_secours_météo |
|---|---|---|---|---|---|---|---|
| DE | background | 4,679 | 7 | 7 | 7 | 23.3 | 0.530 |
| DE | traffic | 2,119 | 3 | 3 | 3 | 0.000 | 0.000 |
| FR | background | 5,486 | 8 | 8 | 6 | 37.1 | 0.000 |
| FR | traffic | 1,366 | 2 | 2 | 2 | 47.9 | 0.000 |

### Contrôle d'horloge, par paire (variations horaires, verdict selon `clock.*`)

| clock_flag | best_lag_h | paires | heures | r_lag0_médian |
|---|---|---|---|---|
| clock_unverifiable | 0 | 1 | 433 | 0.605 |
| ok | 0 | 19 | 12,954 | 0.430 |

### Contrôle d'horloge, par station (paires regroupées)

| clock_flag | stations | heures |
|---|---|---|
| clock_unverifiable | 1 | 433 |
| ok | 17 | 12,954 |

Stations en décalage (toutes leurs paires sont exclues) :

_(rien)_

## 3. Référence EEA (une ligne = une heure d'un point de mesure)

| step | raison | removed | remaining | points_lost | points_remaining | polluant |
|---|---|---|---|---|---|---|
| start | point de départ | 0 | 13,392 | 0 | 18 | pm25 |
| invalid | drapeau de validité refusé ou sentinelle −999 | 93 | 13,299 | 0 | 18 | pm25 |
| not_verified | heure non vérifiée par l'agence | 0 | 13,299 | 0 | 18 | pm25 |
| negative_beyond_uncertainty | valeur < −5 µg/m³ (`eea.negative_floor_ugm3`) : au-delà du bruit autour de 0 | 0 | 13,299 | 0 | 18 | pm25 |
| usable | **utilisable** | 0 | 13,299 | 0 | 18 | pm25 |
| start | point de départ | 0 | 11,904 | 0 | 16 | pm10 |
| invalid | drapeau de validité refusé ou sentinelle −999 | 115 | 11,789 | 0 | 16 | pm10 |
| not_verified | heure non vérifiée par l'agence | 0 | 11,789 | 0 | 16 | pm10 |
| negative_beyond_uncertainty | valeur < −5 µg/m³ (`eea.negative_floor_ugm3`) : au-delà du bruit autour de 0 | 0 | 11,789 | 0 | 16 | pm10 |
| usable | **utilisable** | 0 | 11,789 | 0 | 16 | pm10 |

## 4. Humidité officielle (une ligne = une heure d'une station météo)

| step | raison | removed | remaining | stations_lost | stations_remaining |
|---|---|---|---|---|---|
| start | point de départ | 0 | 24,552 | 0 | 33 |
| missing | humidité officielle absente (dont −999 DWD) | 37 | 24,515 | 0 | 33 |
| doubtful | code qualité Météo-France « douteux » (2) | 0 | 24,515 | 0 | 33 |
| out_of_range | humidité officielle hors 0–100 % | 0 | 24,515 | 0 | 33 |
| usable | **utilisable** | 0 | 24,515 | 0 | 33 |

## 5. Humidité des sondes (feature secondaire)

### Mesures

| step | raison | removed | remaining | sondes_lost | sondes_remaining |
|---|---|---|---|---|---|
| start | point de départ | 0 | 277,909 | 0 | 17 |
| missing | humidité vide / NaN | 4,343 | 273,566 | 0 | 17 |
| out_of_range | humidité < 0 ou > 100 % | 0 | 273,566 | 0 | 17 |
| stuck_low | humidité figée basse (< 5 %) | 22,929 | 250,637 | 1 | 16 |
| saturated | humidité saturée (≥ 99,9 %) | 142,472 | 108,165 | 1 | 15 |
| stuck | humidité identique pendant ≥ 3 h (`humidity.stuck_min_hours`) | 0 | 108,165 | 0 | 15 |
| usable | **utilisable** | 0 | 108,165 | 0 | 15 |

### Heures-sondes

| step | raison | removed | remaining | sondes_lost | sondes_remaining |
|---|---|---|---|---|---|
| start | point de départ | 0 | 11,898 | 0 | 17 |
| dead_month | mois-sonde « mort » (> 30 % d'heures en défaut, `humidity.dead_month_share`) | 7,671 | 4,227 | 11 | 6 |
| faulty_hour | heure en défaut (majorité des mesures non utilisables) dans un mois sain | 201 | 4,026 | 0 | 6 |
| usable | **utilisable** | 0 | 4,026 | 0 | 6 |

## Espace disque

| couche | go |
|---|---|
| bronze | 0.005 |
| silver | 0.004 |
