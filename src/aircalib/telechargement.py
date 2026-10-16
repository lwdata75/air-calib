"""Accès aux trois sources : Sensor.Community, l'EEA et le DWD.

Toutes les requêtes passent par la même fonction `recuperer`, qui limite le nombre de requêtes
simultanées et réessaie en cas d'erreur passagère. Les serveurs de Sensor.Community sont gérés par
une association : on reste poli (au plus 8 requêtes à la fois, et un User-Agent qui dit qui on est).
"""

import asyncio
import io
import random
import re
import zipfile
import zlib
from contextlib import asynccontextmanager
from datetime import date

import httpx
import polars as pl

from aircalib import parametres as P

# Codes HTTP qui valent la peine d'être réessayés (un 404 veut juste dire « ce fichier n'existe pas »).
A_REESSAYER = {408, 425, 429, 500, 502, 503, 504}


@asynccontextmanager
async def ouvrir_client():
    """Client HTTP/1.1 (l'archive casse en HTTP/2) avec notre User-Agent."""
    async with httpx.AsyncClient(
        headers={"User-Agent": P.USER_AGENT},
        timeout=httpx.Timeout(P.DELAI_MAX_S),
        limits=httpx.Limits(max_connections=P.REQUETES_SIMULTANEES),
        follow_redirects=True,
    ) as client:
        yield client


# Un seul « robinet » pour tout le programme : jamais plus de 8 requêtes en même temps.
_robinet: asyncio.Semaphore | None = None


def robinet() -> asyncio.Semaphore:
    """Le sémaphore partagé par toutes les requêtes (créé au premier appel, dans la boucle asyncio)."""
    global _robinet
    if _robinet is None:
        _robinet = asyncio.Semaphore(P.REQUETES_SIMULTANEES)
    return _robinet


async def recuperer(client: httpx.AsyncClient, url: str, entetes: dict | None = None) -> httpx.Response:
    """GET avec réessais (attente doublée à chaque échec) ; renvoie la réponse, même si c'est un 404."""
    for essai in range(P.ESSAIS_MAX + 1):
        try:
            async with robinet():
                reponse = await client.get(url, headers=entetes)
            if reponse.status_code not in A_REESSAYER:
                return reponse
        except httpx.TransportError:
            pass
        if essai < P.ESSAIS_MAX:
            await asyncio.sleep(min(60, 2**essai) * random.uniform(0.5, 1.0))
    raise RuntimeError(f"abandon après {P.ESSAIS_MAX} essais : {url}")


# --- Sensor.Community ---------------------------------------------------------------------------
# Un fichier par capteur et par jour : /<année>/<AAAA-MM-JJ>/<AAAA-MM-JJ>_sds011_sensor_<id>.csv.gz
FICHIER_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_([a-z0-9_-]+?)_sensor_(\d+)(_indoor)?\.csv(\.gz)?")


def url_jour(jour: date) -> str:
    """Dossier d'un jour de l'archive (les années passées sont rangées dans un dossier par année)."""
    if jour.year < date.today().year:
        return f"{P.ARCHIVE_SC}/{jour.year}/{jour.isoformat()}/"
    return f"{P.ARCHIVE_SC}/{jour.isoformat()}/"


def url_fichier(capteur: int, jour: date) -> str:
    """Fichier d'un capteur SDS011 pour un jour (compressé en gzip pour les années passées)."""
    gz = ".gz" if jour.year < date.today().year else ""
    return f"{url_jour(jour)}{jour.isoformat()}_{P.TYPE_CAPTEUR}_sensor_{capteur}.csv{gz}"


async def lister_jour(client: httpx.AsyncClient, jour: date) -> pl.DataFrame:
    """Les capteurs présents dans l'archive ce jour-là (type, identifiant, intérieur ou non)."""
    reponse = await recuperer(client, url_jour(jour))
    if reponse.status_code == 404:
        return pl.DataFrame(
            schema={"jour": pl.Date, "type": pl.String, "capteur": pl.Int64, "interieur": pl.Boolean}
        )
    reponse.raise_for_status()
    lignes = [
        {"jour": jour, "type": m[2], "capteur": int(m[3]), "interieur": m[4] is not None}
        for m in FICHIER_RE.finditer(reponse.text)
    ]
    return pl.DataFrame(lignes).unique()


async def lire_position(client: httpx.AsyncClient, capteur: int, jour: date) -> dict | None:
    """Position d'un capteur, lue dans les 2 premiers Ko de son fichier (une seule requête « Range »)."""
    url = url_fichier(capteur, jour)
    reponse = await recuperer(client, url, {"Range": f"bytes=0-{P.OCTETS_EN_TETE - 1}"})
    if reponse.status_code == 404:
        return None
    contenu = reponse.content
    if url.endswith(".gz"):  # un morceau de fichier gzip se décompresse quand même
        contenu = zlib.decompressobj(wbits=31).decompress(contenu)
    lignes = contenu.decode("utf-8", "replace").split("\n")[:-1]  # la dernière ligne est coupée
    if len(lignes) < 2:
        return None
    entete, valeurs = lignes[0].split(";"), lignes[1].split(";")
    ligne = dict(zip(entete, valeurs, strict=False))
    try:
        return {"capteur": capteur, "lat": float(ligne["lat"]), "lon": float(ligne["lon"])}
    except (KeyError, ValueError):
        return None


def lire_fichier_jour(contenu: bytes, compresse: bool) -> pl.DataFrame:
    """Mesures d'un fichier jour : horodatage UTC, PM10 (P1), PM2.5 (P2) et position.

    Certains logiciels de capteurs écrivent une virgule décimale (« 0,8 ») : on la remplace par un point.
    Une valeur illisible devient vide, elle sera comptée au nettoyage.
    """
    brut = zlib.decompress(contenu, wbits=31) if compresse else contenu
    if not brut.strip():
        return pl.DataFrame()
    table = pl.read_csv(io.BytesIO(brut), separator=";", infer_schema=False)
    if not {"timestamp", "P1", "P2"} <= set(table.columns):
        return pl.DataFrame()

    def nombre(col: str) -> pl.Expr:
        return (
            pl.col(col).str.strip_chars().str.replace(",", ".", literal=True).cast(pl.Float64, strict=False)
        )

    return table.select(
        horodatage=pl.col("timestamp").str.to_datetime("%Y-%m-%dT%H:%M:%S", strict=False),
        pm10=nombre("P1"),
        pm25=nombre("P2"),
        lat=nombre("lat"),
        lon=nombre("lon"),
    )


async def instantane_live(client: httpx.AsyncClient) -> pl.DataFrame:
    """Pays actuel de chaque capteur actif (sert seulement à ne pas lire les capteurs étrangers)."""
    reponse = await recuperer(client, P.INSTANTANE_SC)
    reponse.raise_for_status()
    lignes = [
        {"capteur": m["sensor"]["id"], "pays": m["location"].get("country")}
        for m in reponse.json()
        if m.get("sensor") and m.get("location")
    ]
    return pl.DataFrame(lignes).unique("capteur")


# --- EEA ------------------------------------------------------------------------------------------
async def stations_eea(client: httpx.AsyncClient) -> pl.DataFrame:
    """Points de mesure PM2.5 automatiques (une valeur par heure) en Allemagne, avec leur position."""
    reponse = await recuperer(client, P.EEA_METADONNEES)
    reponse.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(reponse.content)) as archive:
        (nom,) = archive.namelist()
        meta = pl.read_csv(io.BytesIO(archive.read(nom)), infer_schema=False)
    return (
        meta.filter(
            pl.col("Air Pollutant") == "PM2.5",
            pl.col("Measurement Type") == "automatic",
            pl.col("Air Quality Station EoI Code").str.starts_with(P.PAYS),
        )
        .sort("Year", descending=True)
        .unique("Sampling Point Id", keep="first")
        .select(
            point=pl.col("Sampling Point Id"),
            station=pl.col("Air Quality Station EoI Code"),
            nom=pl.col("Air Quality Station Name"),
            type_station=pl.col("Air Quality Station Type"),
            methode=pl.col("Measurement Method"),
            lat=pl.col("Latitude").cast(pl.Float64, strict=False),
            lon=pl.col("Longitude").cast(pl.Float64, strict=False),
        )
        .drop_nulls(["lat", "lon"])
        .sort("point")
    )


async def urls_eea(client: httpx.AsyncClient) -> dict[str, str]:
    """URL du fichier Parquet E1a (données vérifiées) de chaque point de mesure PM2.5 allemand."""
    corps = {
        "countries": [P.PAYS],
        "cities": [],
        "pollutants": [P.EEA_PM25],
        "dataset": 2,  # 2 = E1a, les données vérifiées par chaque pays
        "source": "API",
        "aggregationType": "hour",
    }
    async with robinet():
        reponse = await client.post(f"{P.EEA_API}/ParquetFile/urls", json=corps)
    reponse.raise_for_status()
    urls = [ligne.strip() for ligne in reponse.text.lstrip("﻿").splitlines()[1:] if ligne.startswith("http")]
    # Le nom du fichier est l'identifiant du point de mesure.
    return {url.rsplit("/", 1)[-1].removesuffix(".parquet"): url for url in urls}


def lire_eea(contenu: bytes) -> pl.DataFrame:
    """Valeurs horaires d'une station EEA sur la période, telles que publiées (−999 et drapeaux compris)."""
    table = pl.read_parquet(io.BytesIO(contenu))
    return table.select(
        point=pl.col("Samplingpoint").str.split("/").list.last(),
        debut=pl.col("Start").cast(pl.Datetime("us")),
        valeur=pl.col("Value").cast(pl.Float64),
        validite=pl.col("Validity"),
        verification=pl.col("Verification"),
    ).filter(pl.col("debut").dt.date().is_between(P.DEBUT, date(P.FIN.year + 1, 1, 1)))


# --- DWD (service météo allemand) ---------------------------------------------------------------
STATION_DWD_RE = re.compile(
    r"^\s*(\d+)\s+(\d{8})\s+(\d{8})\s+-?\d+\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(.+?)\s{2,}", re.MULTILINE
)
ZIP_DWD_RE = re.compile(r"stundenwerte_TU_(\d{5})_(\d{8})_(\d{8})_hist\.zip")


async def stations_dwd(client: httpx.AsyncClient) -> pl.DataFrame:
    """Stations du DWD qui mesurent l'humidité à l'heure, avec le fichier qui couvre notre période."""
    liste = await recuperer(client, f"{P.DWD}/TU_Stundenwerte_Beschreibung_Stationen.txt")
    texte = liste.content.decode("latin-1")
    stations = pl.DataFrame(
        [
            {"id_dwd": int(m[1]), "lat": float(m[4]), "lon": float(m[5]), "nom": m[6]}
            for m in STATION_DWD_RE.finditer(texte)
        ]
    )
    index = await recuperer(client, f"{P.DWD}/")
    fichiers = pl.DataFrame(
        [
            {"id_dwd": int(m[1]), "fichier": m[0], "debut": m[2], "fin": m[3]}
            for m in ZIP_DWD_RE.finditer(index.text)
        ]
    ).unique("fichier")
    couvre = fichiers.filter(
        pl.col("debut").str.to_date("%Y%m%d") <= P.DEBUT, pl.col("fin").str.to_date("%Y%m%d") >= P.FIN
    )
    return stations.join(couvre.select("id_dwd", "fichier"), on="id_dwd").unique("id_dwd")


def lire_dwd(contenu: bytes) -> pl.DataFrame:
    """Température et humidité horaires (heure UTC) d'une station DWD sur la période ; −999 = manquant."""
    with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
        (nom,) = [n for n in archive.namelist() if n.startswith("produkt_")]
        table = pl.read_csv(io.BytesIO(archive.read(nom)), separator=";", infer_schema=False)
    table = table.select(pl.all().str.strip_chars())

    def mesure(col: str) -> pl.Expr:
        valeur = pl.col(col).cast(pl.Float64)
        return pl.when(valeur == -999).then(None).otherwise(valeur)

    return table.select(
        id_dwd=pl.col("STATIONS_ID").cast(pl.Int64),
        heure=(pl.col("MESS_DATUM") + "00").str.to_datetime("%Y%m%d%H%M", time_unit="us"),
        temperature=mesure("TT_TU"),
        humidite=mesure("RF_TU"),
    ).filter(pl.col("heure").dt.date().is_between(P.DEBUT, P.FIN))
