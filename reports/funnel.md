# Rapport d'entonnoir — mode complet

_Généré automatiquement le 2026-10-14 13:07 UTC à partir des statuts de la couche silver (aucune ligne supprimée : chaque mesure ou heure garde sa raison)._

## 1. Mesures SDS011 (une ligne = une mesure brute)

| step | raison | removed | remaining | capteurs_lost | capteurs_remaining | segments_lost | segments_remaining |
|---|---|---|---|---|---|---|---|
| start | point de départ | 0 | 377,950,455 | 0 | 1,399 | 0 | 1,479 |
| pm_missing | PM10 ou PM2.5 vide / NaN | 794,913 | 377,155,542 | 1 | 1,398 | 2 | 1,477 |
| duplicate | doublon exact (même horodatage, mêmes valeurs) : la 1re copie est gardée | 156 | 377,155,386 | 0 | 1,398 | 0 | 1,477 |
| duplicate_conflict | même horodatage, valeurs différentes : impossible de choisir | 3,286 | 377,152,100 | 0 | 1,398 | 0 | 1,477 |
| pm_negative | valeur négative (code d'erreur −1 du firmware) | 447,649 | 376,704,451 | 0 | 1,398 | 0 | 1,477 |
| pm_saturated | saturation du capteur (PM2.5 = 999,9 avec PM10 = 1 999,9) | 2,619,590 | 374,084,861 | 2 | 1,396 | 2 | 1,475 |
| pm_above_range | au-delà de la plage du SDS011 (999,9 µg/m³) | 196,594 | 373,888,267 | 0 | 1,396 | 0 | 1,475 |
| pm25_above_pm10 | PM2.5 > PM10 : incohérent (PM2.5 fait partie de PM10) | 786,986 | 373,101,281 | 0 | 1,396 | 0 | 1,475 |
| pm_stuck | valeurs identiques ≥ 1 h ET (code de panne 0/0 OU station qui varie ≥ 5 µg/m³) : figé | 2,555,802 | 370,545,479 | 0 | 1,396 | 0 | 1,475 |
| usable | **utilisable** | 0 | 370,545,479 | 0 | 1,396 | 0 | 1,475 |

## 2. Paires × heures (une ligne = une heure d'un segment de capteur)

| step | raison | removed | remaining | capteurs_lost | capteurs_remaining | paires_lost | paires_remaining | stations_lost | stations_remaining |
|---|---|---|---|---|---|---|---|---|---|
| start | point de départ | 0 | 15,668,400 | 0 | 1,399 | 0 | 1,479 | 0 | 298 |
| no_valid_reading | aucune mesure valide dans l'heure (raison dominante dans `main_flag`) | 230,389 | 15,438,011 | 3 | 1,396 | 4 | 1,475 | 0 | 298 |
| incomplete_hour | complétude < 75 % de la cadence propre du capteur | 800,252 | 14,637,759 | 2 | 1,394 | 8 | 1,467 | 1 | 297 |
| no_reference_station | segment de position sans station utilisable à ≤ 3 km | 42,902 | 14,594,857 | 0 | 1,394 | 19 | 1,448 | 0 | 297 |
| reference_not_downloaded | station de référence non téléchargée | 0 | 14,594,857 | 0 | 1,394 | 0 | 1,448 | 0 | 297 |
| reference_missing | pas de valeur EEA PM2.5 à cette heure | 264,171 | 14,330,686 | 0 | 1,394 | 0 | 1,448 | 0 | 297 |
| reference_invalid | valeur EEA PM2.5 invalide (drapeau ou −999) | 410,665 | 13,920,021 | 0 | 1,394 | 5 | 1,443 | 0 | 297 |
| reference_not_verified | valeur EEA PM2.5 non vérifiée | 0 | 13,920,021 | 0 | 1,394 | 0 | 1,443 | 0 | 297 |
| reference_negative_beyond_uncertainty | valeur EEA PM2.5 < −5 µg/m³ | 3 | 13,920,018 | 0 | 1,394 | 0 | 1,443 | 0 | 297 |
| clock_mismatch_station | station exclue : décalage (toutes paires) ≠ 0, hors retard d'instrument | 24,283 | 13,895,735 | 2 | 1,392 | 2 | 1,441 | 2 | 295 |
| clock_mismatch | paire exclue : meilleur décalage ≠ 0, hors retard d'instrument (TEOM) | 70,038 | 13,825,697 | 10 | 1,382 | 10 | 1,431 | 0 | 295 |
| no_official_humidity | aucune humidité officielle (ni station principale, ni secours) | 3,345 | 13,822,352 | 0 | 1,382 | 0 | 1,431 | 0 | 295 |
| usable | **utilisable** | 0 | 13,822,352 | 0 | 1,382 | 0 | 1,431 | 0 | 295 |

### Heures-paires utilisables par pays et bande de distance

| country | distance_band | heures_paires | paires | capteurs | stations | pct_humidité_locale_fiable | pct_secours_météo |
|---|---|---|---|---|---|---|---|
| DE | far | 7,162,545 | 709 | 689 | 187 | 26.3 | 4.510 |
| DE | near | 5,558,171 | 577 | 555 | 177 | 26.9 | 4.940 |
| FR | far | 694,585 | 96 | 92 | 40 | 33.5 | 0.030 |
| FR | near | 407,051 | 49 | 49 | 35 | 45.7 | 0.020 |

### Heures-paires utilisables par pays et type de station

| country | station_type | heures_paires | paires | capteurs | stations | pct_humidité_locale_fiable | pct_secours_météo |
|---|---|---|---|---|---|---|---|
| DE | background | 5,480,574 | 549 | 538 | 130 | 29.6 | 2.670 |
| DE | industrial | 303,186 | 33 | 33 | 13 | 24.8 | 6.750 |
| DE | traffic | 6,936,956 | 704 | 678 | 94 | 24.3 | 6.210 |
| FR | background | 772,613 | 95 | 92 | 36 | 37.5 | 0.030 |
| FR | industrial | 15,732 | 1 | 1 | 1 | 100.0 | 0.010 |
| FR | traffic | 313,291 | 49 | 47 | 21 | 36.2 | 0.000 |

### Heures-paires utilisables par pays et méthode de mesure de la station

| country | method_class | heures_paires | paires | capteurs | stations | pct_humidité_locale_fiable | pct_secours_météo |
|---|---|---|---|---|---|---|---|
| DE | beta | 623,319 | 55 | 55 | 10 | 19.0 | 0.490 |
| DE | nephelometry_beta | 1,969,271 | 188 | 181 | 34 | 23.9 | 0.460 |
| DE | optical | 9,375,432 | 968 | 932 | 183 | 26.9 | 6.240 |
| DE | teom | 752,694 | 75 | 75 | 10 | 36.2 | 0.030 |
| FR | beta | 216,811 | 40 | 38 | 17 | 41.2 | 0.000 |
| FR | optical | 494,799 | 59 | 57 | 21 | 31.4 | 0.040 |
| FR | teom | 362,684 | 43 | 42 | 17 | 40.5 | 0.010 |
| FR | unknown | 27,342 | 3 | 3 | 3 | 99.3 | 0.000 |

### Contrôle d'horloge, par paire (variations horaires, verdict selon `clock.*`)

| clock_flag | best_lag_h | paires | heures | r_lag0_médian |
|---|---|---|---|---|
| clock_mismatch | -1 | 7 | 45,358 | 0.208 |
| clock_mismatch | 1 | 5 | 45,770 | 0.166 |
| clock_unverifiable | -3 | 25 | 130,935 | 0.009 |
| clock_unverifiable | -2 | 20 | 96,017 | 0.007 |
| clock_unverifiable | -1 | 23 | 183,732 | 0.012 |
| clock_unverifiable | 0 | 186 | 1,605,481 | 0.060 |
| clock_unverifiable | 1 | 22 | 131,233 | 0.035 |
| clock_unverifiable | 2 | 8 | 39,552 | 0.003 |
| clock_unverifiable | 3 | 15 | 74,631 | -0.020 |
| instrument_lag | 1 | 17 | 187,772 | 0.220 |
| ok | -3 | 1 | 520 | 0.050 |
| ok | -2 | 5 | 5,686 | 0.075 |
| ok | -1 | 6 | 30,612 | 0.145 |
| ok | 0 | 1,053 | 10,623,830 | 0.398 |
| ok | 1 | 46 | 376,132 | 0.151 |
| ok | 2 | 1 | 3,667 | 0.096 |
| ok | 3 | 1 | 519 | 0.125 |

### Contrôle d'horloge, par station (paires regroupées)

| clock_flag | stations | heures |
|---|---|---|
| clock_mismatch | 2 | 23,230 |
| clock_unverifiable | 81 | 3,955,955 |
| instrument_lag | 5 | 478,863 |
| ok | 209 | 9,123,399 |

Stations en décalage (toutes leurs paires sont exclues) :

| station_code | r_lag0 | n_hours | best_lag_h | r_best | z | clock_flag | method_class | measurement_method |
|---|---|---|---|---|---|---|---|---|
| DEBY099 | 0.104 | 7,583 | 1 | 0.309 | 13.2 | clock_mismatch | nephelometry_beta | nephelometry+beta |
| DEMV007 | 0.114 | 15,647 | -1 | 0.155 | 3.697 | clock_mismatch | optical | light-scat |

## 3. Référence EEA (une ligne = une heure d'un point de mesure)

| step | raison | removed | remaining | points_lost | points_remaining | polluant |
|---|---|---|---|---|---|---|
| start | point de départ | 0 | 5,034,394 | 0 | 298 | pm25 |
| invalid | drapeau de validité refusé ou sentinelle −999 | 160,414 | 4,873,980 | 0 | 298 | pm25 |
| not_verified | heure non vérifiée par l'agence | 0 | 4,873,980 | 0 | 298 | pm25 |
| negative_beyond_uncertainty | valeur < −5 µg/m³ (`eea.negative_floor_ugm3`) : au-delà du bruit autour de 0 | 1 | 4,873,979 | 0 | 298 | pm25 |
| usable | **utilisable** | 0 | 4,873,979 | 0 | 298 | pm25 |
| start | point de départ | 0 | 4,764,209 | 0 | 277 | pm10 |
| invalid | drapeau de validité refusé ou sentinelle −999 | 88,798 | 4,675,411 | 0 | 277 | pm10 |
| not_verified | heure non vérifiée par l'agence | 0 | 4,675,411 | 0 | 277 | pm10 |
| negative_beyond_uncertainty | valeur < −5 µg/m³ (`eea.negative_floor_ugm3`) : au-delà du bruit autour de 0 | 1 | 4,675,410 | 0 | 277 | pm10 |
| usable | **utilisable** | 0 | 4,675,410 | 0 | 277 | pm10 |

## 4. Humidité officielle (une ligne = une heure d'une station météo)

| step | raison | removed | remaining | stations_lost | stations_remaining |
|---|---|---|---|---|---|
| start | point de départ | 0 | 5,285,280 | 0 | 307 |
| missing | humidité officielle absente (dont −999 DWD) | 13,954 | 5,271,326 | 0 | 307 |
| doubtful | code qualité Météo-France « douteux » (2) | 0 | 5,271,326 | 0 | 307 |
| out_of_range | humidité officielle hors 0–100 % | 0 | 5,271,326 | 0 | 307 |
| usable | **utilisable** | 0 | 5,271,326 | 0 | 307 |

## 5. Humidité des sondes (feature secondaire)

### Mesures

| step | raison | removed | remaining | sondes_lost | sondes_remaining |
|---|---|---|---|---|---|
| start | point de départ | 0 | 289,721,376 | 0 | 1,127 |
| missing | humidité vide / NaN | 2,043,899 | 287,677,477 | 1 | 1,126 |
| out_of_range | humidité < 0 ou > 100 % | 669,492 | 287,007,985 | 0 | 1,126 |
| stuck_low | humidité figée basse (< 5 %) | 12,455,804 | 274,552,181 | 5 | 1,121 |
| saturated | humidité saturée (≥ 99,9 %) | 124,582,110 | 149,970,071 | 12 | 1,109 |
| stuck | humidité identique pendant ≥ 3 h (`humidity.stuck_min_hours`) | 864,873 | 149,105,198 | 0 | 1,109 |
| usable | **utilisable** | 0 | 149,105,198 | 0 | 1,109 |

### Heures-sondes

| step | raison | removed | remaining | sondes_lost | sondes_remaining |
|---|---|---|---|---|---|
| start | point de départ | 0 | 11,988,970 | 0 | 1,126 |
| dead_month | mois-sonde « mort » (> 30 % d'heures en défaut, `humidity.dead_month_share`) | 7,144,741 | 4,844,229 | 467 | 659 |
| faulty_hour | heure en défaut (majorité des mesures non utilisables) dans un mois sain | 225,809 | 4,618,420 | 0 | 659 |
| usable | **utilisable** | 0 | 4,618,420 | 0 | 659 |

## Espace disque

| couche | go |
|---|---|
| bronze | 5.317 |
| silver | 2.740 |
