"""Étape 1 : trouver les capteurs SDS011 proches d'une station officielle, puis tout télécharger.

    python scripts/01_telecharger.py              # volume complet (≈ 1 h)
    python scripts/01_telecharger.py --echantillon # 10 capteurs, un mois (quelques minutes)

Ce qu'on récupère, dans data/brut/ :
  - stations_eea.parquet : les stations PM2.5 allemandes ;
  - listes/ : les capteurs présents dans l'archive le 15 de chaque mois ;
  - positions.parquet : la position de chaque capteur candidat ;
  - paires.parquet : capteur ↔ station la plus proche (≤ 1,5 km) ↔ station météo la plus proche ;
  - capteurs/<id>/<AAAA-MM>.parquet, eea/<point>.parquet, dwd/<id>.parquet : les mesures.

On peut relancer le script après une coupure : un fichier déjà présent n'est jamais redemandé.
"""

import argparse
import asyncio
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from aircalib import parametres as P
from aircalib import telechargement as T
from aircalib.geo import distance_km, plus_proche


def les_15_du_mois(echantillon: bool) -> list[date]:
    """Les jours dont on lit la liste de fichiers : le 15 de chaque mois de la période."""
    if echantillon:
        return [P.ECHANTILLON_MOIS.replace(day=15)]
    jours, jour = [], P.DEBUT.replace(day=15)
    while jour <= P.FIN:
        jours.append(jour)
        jour = (jour.replace(day=1) + timedelta(days=32)).replace(day=15)
    return jours


async def lire_listes(client, dossier: Path, jours: list[date]) -> pl.DataFrame:
    """Les fichiers SDS011 présents dans l'archive ces jours-là (chaque liste est gardée en cache)."""
    dossier.mkdir(parents=True, exist_ok=True)
    limite = asyncio.Semaphore(P.LISTINGS_SIMULTANES)  # une liste pèse ~5 Mo : pas plus de 2 à la fois

    async def une_liste(jour: date) -> pl.DataFrame:
        cache = dossier / f"{jour}.parquet"
        if not cache.exists():
            async with limite:
                liste = await T.lister_jour(client, jour)
            liste.write_parquet(cache)
            print(f"  liste du {jour} : {liste.height} fichiers", flush=True)
        return pl.read_parquet(cache)

    listes = await asyncio.gather(*(une_liste(j) for j in jours))
    return pl.concat(listes).filter(pl.col("type") == P.TYPE_CAPTEUR, ~pl.col("interieur"))


async def lire_positions(client, cache: Path, capteurs: pl.DataFrame) -> pl.DataFrame:
    """Position de chaque capteur (lue au début de son fichier le plus récent), gardée en cache."""
    schema = {"capteur": pl.Int64, "lat": pl.Float64, "lon": pl.Float64}
    connues = pl.read_parquet(cache) if cache.exists() else pl.DataFrame(schema=schema)
    a_lire = capteurs.join(connues.select("capteur"), on="capteur", how="anti")
    if a_lire.height:
        print(f"  lecture de {a_lire.height} positions…", flush=True)
        resultats = await asyncio.gather(
            *(T.lire_position(client, c, j) for c, j in a_lire.select("capteur", "dernier_jour").iter_rows())
        )
        nouvelles = pl.DataFrame([r for r in resultats if r], schema=schema)
        connues = pl.concat([connues, nouvelles])
        connues.write_parquet(cache)
    return connues


def fenetre(premier: date, dernier: date) -> tuple[date, date]:
    """Les jours à demander pour un capteur vu le 15 du mois, du mois `premier` au mois `dernier`.

    Il a pu démarrer juste après le 15 du mois précédent et s'arrêter juste avant le 15 du mois suivant :
    on demande donc large, les jours sans fichier renvoient simplement un 404.
    """
    debut = (premier.replace(day=1) - timedelta(days=1)).replace(day=16)
    fin = (dernier.replace(day=1) + timedelta(days=32)).replace(day=14)
    return max(debut, P.DEBUT), min(fin, P.FIN)


async def telecharger_capteur(client, dossier: Path, capteur: int, debut: date, fin: date) -> int:
    """Télécharge les jours d'un capteur, mois par mois ; renvoie le nombre de jours trouvés."""
    trouves, mois = 0, debut.replace(day=1)
    while mois <= fin:
        fichier = dossier / str(capteur) / f"{mois:%Y-%m}.parquet"
        fin_mois = (mois + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if not fichier.exists():
            jours = [mois + timedelta(days=i) for i in range((fin_mois - mois).days + 1)]
            jours = [j for j in jours if debut <= j <= fin]
            reponses = await asyncio.gather(*(T.recuperer(client, T.url_fichier(capteur, j)) for j in jours))
            tables = [
                T.lire_fichier_jour(r.content, compresse=str(r.url).endswith(".gz"))
                for r in reponses
                if r.status_code == 200
            ]
            tables = [t for t in tables if t.height]
            trouves += len(tables)
            vide = pl.DataFrame(
                schema={
                    "horodatage": pl.Datetime("us"),
                    "pm10": pl.Float64,
                    "pm25": pl.Float64,
                    "lat": pl.Float64,
                    "lon": pl.Float64,
                }
            )
            fichier.parent.mkdir(parents=True, exist_ok=True)
            (pl.concat(tables) if tables else vide).write_parquet(
                fichier
            )  # même vide : on ne redemandera pas
        mois = fin_mois + timedelta(days=1)
    return trouves


async def telecharger_sondes(client, dossier: Path, live: pl.DataFrame, par_type: int) -> None:
    """Un mois (janvier 2025) de sondes d'humidité actives en Allemagne, tirées au hasard."""
    debut = P.SONDES_MOIS
    jours = [debut + timedelta(days=i) for i in range(31)]
    sondes = (
        live.filter(pl.col("pays") == P.PAYS, pl.col("type").is_in(P.SONDES_TYPES), ~pl.col("interieur"))
        .drop_nulls(["lat", "lon"])
        .sort("capteur")
        .group_by("type", maintain_order=True)
        .map_groups(lambda g: g.sample(n=min(par_type, g.height), seed=P.GRAINE))
    )
    for sonde, type_sonde, lat, lon in sondes.select("capteur", "type", "lat", "lon").iter_rows():
        fichier = dossier / f"{sonde}.parquet"
        if fichier.exists():
            continue
        reponses = await asyncio.gather(
            *(T.recuperer(client, T.url_fichier(sonde, j, type_sonde)) for j in jours)
        )
        tables = [T.lire_sonde_jour(r.content) for r in reponses if r.status_code == 200]
        tables = [t for t in tables if t.height]
        dossier.mkdir(parents=True, exist_ok=True)
        if tables:
            pl.concat(tables).with_columns(
                capteur=pl.lit(sonde), type=pl.lit(type_sonde), lat=pl.lit(lat), lon=pl.lit(lon)
            ).write_parquet(fichier)
    print(f"   {len(list(dossier.glob('*.parquet')))} sondes avec des mesures", flush=True)


async def principal(echantillon: bool) -> None:
    racine = P.dossier_donnees(echantillon) / "brut"
    racine.mkdir(parents=True, exist_ok=True)
    depart = time.time()
    async with T.ouvrir_client() as client:
        print("1. Stations EEA (PM2.5, Allemagne)…", flush=True)
        stations = await T.stations_eea(client)
        stations.write_parquet(racine / "stations_eea.parquet")
        print(f"   {stations.height} points de mesure", flush=True)

        print("2. Capteurs présents dans l'archive le 15 de chaque mois…", flush=True)
        listes = await lire_listes(client, racine / "listes", les_15_du_mois(echantillon))
        capteurs = listes.group_by("capteur").agg(
            premier_jour=pl.col("jour").min(), dernier_jour=pl.col("jour").max()
        )
        print(f"   {capteurs.height} capteurs SDS011 extérieurs dans le monde", flush=True)

        print("3. Positions (on saute les capteurs actifs aujourd'hui hors d'Allemagne)…", flush=True)
        live = await T.instantane_live(client)
        etrangers = live.filter(pl.col("pays") != P.PAYS)
        candidats = capteurs.join(etrangers, on="capteur", how="anti")
        if echantillon:  # on ne lit que des capteurs actifs en Allemagne, tirés au hasard
            candidats = (
                candidats.join(live.filter(pl.col("pays") == P.PAYS), on="capteur")
                .sample(fraction=1.0, shuffle=True, seed=P.GRAINE)
                .head(400)
            )
        positions = await lire_positions(client, racine / "positions.parquet", candidats)

        print("4. Référence EEA des stations proches d'un capteur…", flush=True)
        urls = await T.urls_eea(client)
        # Tous les points à 1,5 km ou moins d'un capteur, pas seulement le plus proche : une station peut
        # avoir un ancien identifiant sans données, et une voisine avec des données.
        proches = (
            positions.join(stations.select("point", lat_s="lat", lon_s="lon"), how="cross")
            .filter(distance_km(pl.col("lat"), pl.col("lon"), pl.col("lat_s"), pl.col("lon_s")) <= P.RAYON_KM)
            .select("point")
            .unique()
        )
        a_telecharger = sorted(set(proches["point"]) & set(urls))
        for point in a_telecharger:
            fichier = racine / "eea" / f"{point}.parquet"
            if not fichier.exists():
                reponse = await T.recuperer(client, urls[point])
                reponse.raise_for_status()
                fichier.parent.mkdir(parents=True, exist_ok=True)
                T.lire_eea(reponse.content).write_parquet(fichier)
        # Une station n'est utile que si elle a assez d'heures valides et vérifiées sur 2024-2025.
        heures = pl.concat(
            [
                pl.scan_parquet(racine / "eea" / f"{point}.parquet")
                .filter(pl.col("validite").is_in(P.EEA_VALIDITES), pl.col("verification") == 1)
                .select(point=pl.lit(point), heures=pl.len())
                .collect()
                for point in a_telecharger
            ]
        )
        utiles = heures.filter(pl.col("heures") >= P.HEURES_MIN_STATION)["point"]
        print(
            f"   {len(a_telecharger)} stations téléchargées, {utiles.len()} avec des données sur la période",
            flush=True,
        )

        print("5. Appariement capteur ↔ station la plus proche (≤ 1,5 km)…", flush=True)
        paires = plus_proche(
            capteurs.join(positions, on="capteur"),
            stations.filter(pl.col("point").is_in(utiles.implode())).select(
                "point", "station", "type_station", "methode", "lat", "lon"
            ),
            cle="point",
            rayon_km=P.RAYON_KM,
        )
        if echantillon:
            paires = paires.sort("capteur").head(P.ECHANTILLON_CAPTEURS)
        meteo = await T.stations_dwd(client)
        paires = paires.join(
            plus_proche(
                paires.select("capteur", "lat", "lon"),
                meteo.select("id_dwd", "fichier", "lat", "lon"),
                cle="id_dwd",
            )
            .rename({"distance_km": "distance_meteo_km"})
            .select("capteur", "id_dwd", "fichier", "distance_meteo_km"),
            on="capteur",
        )
        paires.write_parquet(racine / "paires.parquet")
        print(f"   {paires.height} paires, {paires['station'].n_unique()} stations", flush=True)

        print("6. Capteurs voisins (à 3 km ou moins d'un capteur apparié, sans station à eux)…", flush=True)
        # Ils ne servent qu'à calculer la médiane des voisins : on ne les compare jamais à une station.
        autres = capteurs.join(positions, on="capteur").join(
            paires.select("capteur"), on="capteur", how="anti"
        )
        voisins = (
            paires.select("capteur", lat_p="lat", lon_p="lon")
            .join(autres, how="cross", suffix="_v")
            .filter(
                distance_km(pl.col("lat_p"), pl.col("lon_p"), pl.col("lat"), pl.col("lon"))
                <= P.VOISINS_RAYON_KM
            )
            .select(
                capteur="capteur_v",
                premier_jour="premier_jour",
                dernier_jour="dernier_jour",
                lat="lat",
                lon="lon",
            )
            .unique("capteur")
            .sort("capteur")
        )
        voisins.write_parquet(racine / "voisins.parquet")
        print(f"   {voisins.height} capteurs voisins", flush=True)

        print("7. Mesures des capteurs (appariés puis voisins), jour par jour…", flush=True)
        a_telecharger = pl.concat(
            [
                paires.select("capteur", "premier_jour", "dernier_jour"),
                voisins.select("capteur", "premier_jour", "dernier_jour"),
            ]
        )
        limite = asyncio.Semaphore(4)  # 4 capteurs à la fois (leurs requêtes partagent le robinet de 8)
        faits = 0

        async def un_capteur(capteur: int, premier: date, dernier: date) -> int:
            nonlocal faits
            debut, fin = (
                (
                    P.ECHANTILLON_MOIS,
                    (P.ECHANTILLON_MOIS + timedelta(days=32)).replace(day=1) - timedelta(days=1),
                )
                if echantillon
                else fenetre(premier, dernier)
            )
            async with limite:
                n = await telecharger_capteur(client, racine / "capteurs", capteur, debut, fin)
            faits += 1
            if faits % 50 == 0 or faits == a_telecharger.height:
                minutes = (time.time() - depart) / 60
                print(f"   {faits}/{a_telecharger.height} capteurs ({minutes:.0f} min)", flush=True)
            return n

        jours = await asyncio.gather(*(un_capteur(c, p, d) for c, p, d in a_telecharger.iter_rows()))
        print(f"   {sum(jours)} nouveaux fichiers jour", flush=True)

        print("8. Météo officielle du DWD…", flush=True)
        for id_dwd, nom_fichier in paires.select("id_dwd", "fichier").unique().iter_rows():
            fichier = racine / "dwd" / f"{id_dwd}.parquet"
            if fichier.exists():
                continue
            reponse = await T.recuperer(client, f"{P.DWD}/{nom_fichier}")
            reponse.raise_for_status()
            fichier.parent.mkdir(parents=True, exist_ok=True)
            T.lire_dwd(reponse.content).write_parquet(fichier)
        print(f"   {paires['id_dwd'].n_unique()} stations météo", flush=True)

        print(
            "9. Un échantillon de sondes d'humidité (DHT22, BME280), pour vérifier si on peut s'en servir…",
            flush=True,
        )
        await telecharger_sondes(client, racine / "sondes", live, 10 if echantillon else P.SONDES_PAR_TYPE)

    print(f"Terminé en {(time.time() - depart) / 60:.1f} min.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--echantillon", action="store_true", help="10 capteurs sur un mois")
    asyncio.run(principal(parser.parse_args().echantillon))
