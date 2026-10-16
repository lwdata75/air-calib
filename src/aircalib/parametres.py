"""Tous les réglages du projet, au même endroit.

On a préféré un simple fichier Python commenté à un fichier de configuration :
chaque valeur est expliquée juste à côté, et on la retrouve facilement.
"""

from datetime import date
from pathlib import Path

# --- Dossiers ---------------------------------------------------------------------------------
RACINE = Path(__file__).resolve().parents[2]
DONNEES = RACINE / "data"  # tout ce qui est téléchargé ou calculé (jamais versionné)
RAPPORTS = RACINE / "reports"  # petits fichiers versionnés : entonnoir, journal des essais, figures

# --- Périmètre ----------------------------------------------------------------------------------
DEBUT = date(2024, 1, 1)
FIN = date(2025, 12, 31)
PAYS = "DE"  # on travaille uniquement sur l'Allemagne (le réseau citoyen y est le plus dense)
RAYON_KM = 1.5  # un capteur est apparié à la station la plus proche, si elle est à 1,5 km ou moins
TYPE_CAPTEUR = "sds011"  # le capteur de particules le plus répandu du réseau

# Mode « échantillon » : quelques capteurs sur un seul mois, pour tester la chaîne en quelques minutes.
ECHANTILLON_CAPTEURS = 10
ECHANTILLON_MOIS = date(2025, 3, 1)

# --- Sources --------------------------------------------------------------------------------------
ARCHIVE_SC = "https://archive.sensor.community"
INSTANTANE_SC = "https://data.sensor.community/static/v2/data.json"  # capteurs actifs « en ce moment »
EEA_METADONNEES = (
    "https://discomap.eea.europa.eu/App/AQViewer/download?fqn=Airquality_Dissem.b2g.measurements&f=csv"
)
EEA_API = "https://eeadmz1-downloads-api-appservice.azurewebsites.net"
EEA_PM25 = "http://dd.eionet.europa.eu/vocabulary/aq/pollutant/6001"
DWD = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly/air_temperature/historical"

# --- Téléchargement poli ------------------------------------------------------------------------
# En-tête HTTP qui dit qui on est (les en-têtes n'acceptent que de l'ASCII, d'où l'anglais).
USER_AGENT = "air-calib/1.0 (+https://github.com/lwdata75/air-calib; student project)"
REQUETES_SIMULTANEES = 8  # les serveurs de Sensor.Community sont financés par des bénévoles
LISTINGS_SIMULTANES = 2  # une liste de fichiers d'un jour pèse ~5 Mo : le serveur coupe au-delà de 2
ESSAIS_MAX = 5  # nouvelles tentatives en cas d'erreur réseau passagère
DELAI_MAX_S = 180  # une liste de fichiers met environ 65 s à arriver
OCTETS_EN_TETE = 2048  # pour lire la position d'un capteur, le début de son fichier suffit

# --- Nettoyage des mesures du capteur -----------------------------------------------------------
PM25_PLAFOND = 999.9  # le SDS011 bloqué au maximum écrit 999,9 (PM2.5) et 1 999,9 (PM10)
PM10_PLAFOND = 1999.9
PLATEAU_MIN_H = 1.0  # un code de panne 0/0 répété au moins 1 h = capteur figé
COMPLETUDE_MIN = 0.75  # une heure compte si elle a au moins 75 % des mesures attendues
DEPLACEMENT_MAX_KM = 1.0  # un capteur qui a bougé de plus de 1 km est écarté

# --- Référence EEA ------------------------------------------------------------------------------
EEA_DECALAGE_H = 1  # mesuré dans le notebook 01 : heure UTC = Start − 1 h
EEA_VALIDITES = [1, 2, 3]  # codes « valide » (2 et 3 : sous la limite de détection)
EEA_MANQUANT = -999.0
HEURES_MIN_STATION = 500  # une station sert de référence si elle a au moins 500 heures valides

# --- Paires défectueuses ------------------------------------------------------------------------
# Un capteur dont les variations horaires ne suivent pas celles de sa station est probablement en panne.
CORRELATION_MIN = 0.1
HEURES_MIN_CONTROLE = 500

# --- Variables ----------------------------------------------------------------------------------
FUSEAU_LOCAL = "Europe/Berlin"  # pour l'heure de la journée et le jour de la semaine
FENETRES_H = [3, 24]  # moyennes glissantes du PM brut, sur les heures passées seulement
VOISINS_RAYON_KM = 3.0  # les voisins d'un capteur : les autres capteurs à 3 km ou moins
VOISINS_MIN = 3  # on calcule une médiane seulement s'il y a au moins 3 voisins valides

# Sondes d'humidité : un petit échantillon, seulement pour vérifier si on peut s'en servir.
SONDES_TYPES = ["dht22", "bme280"]
SONDES_PAR_TYPE = 100
SONDES_MOIS = date(2025, 1, 1)
SATURATION_PCT = 99.9  # une humidité de 99,9 % ou plus est dite saturée

# --- Évaluation ---------------------------------------------------------------------------------
N_PLIS = 5
GRAINE = 42
PART_ECHANTILLON_STATIONS = 0.2  # le notebook 03 apprend d'abord sur 20 % des stations
# Cibles de l'EPA pour les capteurs de PM2.5 (Duvall et al., 2021).
EPA_R2_MIN = 0.70
EPA_PENTE = (0.65, 1.35)
EPA_ORDONNEE = (-5.0, 5.0)
EPA_RMSE_MAX = 7.0
HEURES_MIN_PAIRE = 500  # une paire est notée en horaire si elle a au moins 500 heures
JOURS_MIN_PAIRE = 30  # … et en journalier si elle a au moins 30 jours complets
HEURES_MIN_JOUR = 18  # un jour est complet avec au moins 18 heures valides
BANDES_DISTANCE_KM = [0.5, 1.0, 1.5]


def dossier_donnees(echantillon: bool = False) -> Path:
    """`data/` pour le volume complet, `data/echantillon/` pour le mode échantillon (jamais mélangés)."""
    return DONNEES / "echantillon" if echantillon else DONNEES
