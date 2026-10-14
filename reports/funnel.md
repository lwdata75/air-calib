# Rapport d'entonnoir — mode complet

_Généré automatiquement le 2026-10-14 12:04 UTC à partir des statuts de la couche silver (aucune ligne supprimée : chaque mesure ou heure garde sa raison)._

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
| pm_stuck | valeurs identiques pendant ≥ 1 h (`particles.stuck_min_hours`) : capteur figé | 3,068,718 | 370,032,563 | 0 | 1,396 | 0 | 1,475 |
| usable | **utilisable** | 0 | 370,032,563 | 0 | 1,396 | 0 | 1,475 |

## 2. Paires × heures (une ligne = une heure d'un segment de capteur)

| step | raison | removed | remaining | capteurs_lost | capteurs_remaining | paires_lost | paires_remaining | stations_lost | stations_remaining |
|---|---|---|---|---|---|---|---|---|---|
| start | point de départ | 0 | 15,668,400 | 0 | 1,399 | 0 | 1,479 | 0 | 298 |
| no_valid_reading | aucune mesure valide dans l'heure (raison dominante dans `main_flag`) | 241,293 | 15,427,107 | 3 | 1,396 | 4 | 1,475 | 0 | 298 |
| incomplete_hour | complétude < 75 % de la cadence propre du capteur | 815,431 | 14,611,676 | 2 | 1,394 | 8 | 1,467 | 1 | 297 |
| no_reference_station | segment de position sans station utilisable à ≤ 3 km | 42,902 | 14,568,774 | 0 | 1,394 | 19 | 1,448 | 0 | 297 |
| reference_not_downloaded | station de référence non téléchargée | 0 | 14,568,774 | 0 | 1,394 | 0 | 1,448 | 0 | 297 |
| reference_missing | pas de valeur EEA PM2.5 à cette heure | 262,774 | 14,306,000 | 0 | 1,394 | 0 | 1,448 | 0 | 297 |
| reference_invalid | valeur EEA PM2.5 invalide (drapeau ou −999) | 409,539 | 13,896,461 | 0 | 1,394 | 5 | 1,443 | 0 | 297 |
| reference_not_verified | valeur EEA PM2.5 non vérifiée | 0 | 13,896,461 | 0 | 1,394 | 0 | 1,443 | 0 | 297 |
| reference_negative_beyond_uncertainty | valeur EEA PM2.5 < −5 µg/m³ | 3 | 13,896,458 | 0 | 1,394 | 0 | 1,443 | 0 | 297 |
| clock_mismatch_station | station exclue : son décalage d'horloge (toutes paires) ≠ 0 | 514,771 | 13,381,687 | 52 | 1,342 | 52 | 1,391 | 7 | 290 |
| clock_mismatch | paire exclue : son meilleur décalage d'horloge ≠ 0 | 100,269 | 13,281,418 | 12 | 1,330 | 12 | 1,379 | 0 | 290 |
| no_official_humidity | aucune humidité officielle (ni station principale, ni secours) | 3,339 | 13,278,079 | 0 | 1,330 | 0 | 1,379 | 0 | 290 |
| usable | **utilisable** | 0 | 13,278,079 | 0 | 1,330 | 0 | 1,379 | 0 | 290 |

### Heures-paires utilisables par pays et bande de distance

| country | distance_band | heures_paires | paires | capteurs | stations | pct_humidité_locale_fiable | pct_secours_météo |
|---|---|---|---|---|---|---|---|
| DE | far | 6,859,362 | 680 | 660 | 185 | 26.3 | 4.690 |
| DE | near | 5,337,876 | 556 | 534 | 171 | 26.8 | 5.140 |
| FR | far | 677,755 | 95 | 91 | 39 | 31.9 | 0.030 |
| FR | near | 403,086 | 48 | 48 | 34 | 46.1 | 0.020 |

### Heures-paires utilisables par pays et type de station

| country | station_type | heures_paires | paires | capteurs | stations | pct_humidité_locale_fiable | pct_secours_météo |
|---|---|---|---|---|---|---|---|
| DE | background | 5,425,402 | 546 | 535 | 129 | 29.8 | 2.680 |
| DE | industrial | 302,643 | 33 | 33 | 13 | 24.9 | 6.760 |
| DE | traffic | 6,469,193 | 657 | 631 | 91 | 23.8 | 6.660 |
| FR | background | 751,924 | 93 | 90 | 35 | 36.3 | 0.030 |
| FR | industrial | 15,732 | 1 | 1 | 1 | 100.0 | 0.010 |
| FR | traffic | 313,185 | 49 | 47 | 21 | 36.2 | 0.000 |

### Contrôle d'horloge, par paire (variations horaires, verdict selon `clock.*`)

| clock_flag | best_lag_h | paires | heures | r_lag0_médian |
|---|---|---|---|---|
| clock_mismatch | -1 | 7 | 45,248 | 0.208 |
| clock_mismatch | 1 | 22 | 233,530 | 0.197 |
| clock_unverifiable | -3 | 25 | 126,923 | 0.009 |
| clock_unverifiable | -2 | 21 | 95,941 | 0.002 |
| clock_unverifiable | -1 | 23 | 182,509 | 0.012 |
| clock_unverifiable | 0 | 185 | 1,598,687 | 0.060 |
| clock_unverifiable | 1 | 21 | 130,720 | 0.035 |
| clock_unverifiable | 2 | 8 | 39,522 | 0.003 |
| clock_unverifiable | 3 | 15 | 73,425 | -0.013 |
| ok | -3 | 1 | 520 | 0.050 |
| ok | -2 | 5 | 5,686 | 0.075 |
| ok | -1 | 6 | 30,612 | 0.145 |
| ok | 0 | 1,053 | 10,608,403 | 0.398 |
| ok | 1 | 46 | 376,114 | 0.151 |
| ok | 2 | 1 | 3,667 | 0.096 |
| ok | 3 | 1 | 519 | 0.125 |

### Contrôle d'horloge, par station (paires regroupées)

| clock_flag | stations | heures |
|---|---|---|
| clock_mismatch | 7 | 501,983 |
| clock_unverifiable | 81 | 3,949,465 |
| ok | 209 | 9,100,578 |

Stations en décalage (toutes leurs paires sont exclues) :

| station_code | r_lag0 | n_hours | best_lag_h | r_best | z | clock_flag | measurement_method |
|---|---|---|---|---|---|---|---|
| DEBY099 | 0.104 | 7,583 | 1 | 0.309 | 13.2 | clock_mismatch | nephelometry+beta |
| DEMV007 | 0.114 | 15,537 | -1 | 0.155 | 3.694 | clock_mismatch | light-scat |
| DESN020 | 0.243 | 4,546 | 1 | 0.322 | 4.143 | clock_mismatch | TEOM |
| DESN025 | 0.130 | 193,279 | 1 | 0.145 | 4.809 | clock_mismatch | TEOM |
| DESN045 | 0.116 | 31,354 | 1 | 0.150 | 4.340 | clock_mismatch | TEOM |
| DESN061 | 0.089 | 230,742 | 1 | 0.108 | 6.479 | clock_mismatch | TEOM |
| FR33220 | 0.080 | 18,942 | 1 | 0.124 | 4.367 | clock_mismatch | TEOM-FDMS |

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
| silver | 4.164 |
