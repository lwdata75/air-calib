# Rapport d'ingestion — mode échantillon (`--sample`)

_Généré automatiquement le 2026-10-14 12:14 UTC à partir des manifestes._

## Sensor.Community (unités = capteur × mois)

| kind | units_ok | units_failed | days_requested | days_found | days_absent | downloaded_mb | decimal_commas | units_converted | conversion_failed | rows | corrupt_files | unparsed_values | dropped_nonempty | foreign_sensor_rows | bronze_mb |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| humidity | 17 | 0 | 527 | 527 | 0 | 2.145 | 0 | 17 | 0 | 277,909 | 0 | 0 | 0 | 0 | 1.870 |
| pm | 20 | 0 | 620 | 618 | 2 | 3.028 | 0 | 20 | 0 | 328,671 | 0 | 0 | 0 | 0 | 2.661 |

## EEA, DWD, Météo-France (unités = fichiers)

| source | layer | ok | failed |
|---|---|---|---|
| eea | raw | 34 | 0 |
| eea | bronze | 34 | 0 |
| dwd | raw | 17 | 0 |
| dwd | bronze | 17 | 0 |
| meteo_france | raw | 21 | 0 |
| meteo_france | bronze | 21 | 0 |

## Espace disque

| folder | gb |
|---|---|
| raw | 0.044 |
| bronze | 0.005 |
| meta | 0.009 |
