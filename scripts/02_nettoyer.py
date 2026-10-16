"""Étape 2 : nettoyer les mesures et construire les heures-paires.

    python scripts/02_nettoyer.py [--echantillon]

Pour chaque capteur : règles physiques (a), plateaux de panne (b), capteur déplacé (f), moyennes
horaires (c). Puis jointure avec la référence EEA remise à l'heure UTC (d, e) et la météo du DWD, et
contrôle de chaque paire (g).

Sorties : data/propre/heures_paires.parquet, data/propre/paires.parquet et reports/entonnoir.csv
(ce que chaque règle a retiré).
"""

import argparse
import time
from concurrent.futures import ThreadPoolExecutor

import polars as pl

from aircalib import nettoyage as N
from aircalib import parametres as P
from aircalib.controles import verifier_heures_paires


def nettoyer_capteur(dossier, paire: dict) -> tuple[pl.DataFrame | None, dict]:
    """Applique les règles a, b, f et c à un capteur ; renvoie ses heures et ce que chaque règle a retiré."""
    fichiers = sorted((dossier / "capteurs" / str(paire["capteur"])).glob("*.parquet"))
    mesures = pl.concat([pl.read_parquet(f) for f in fichiers]) if fichiers else pl.DataFrame()
    compte = {"mesures brutes": mesures.height}
    if mesures.is_empty():
        return None, compte | {"capteur sans mesure": 1}
    for regle, reste in N.retirer_valeurs_impossibles(mesures).items():
        compte[regle] = mesures.height - reste.height
        mesures = reste
    reste = N.retirer_plateaux_de_panne(mesures)
    compte["plateau 0/0 ≥ 1 h"] = mesures.height - reste.height
    mesures = reste
    if N.a_demenage(mesures, paire["lat"], paire["lon"]):
        return None, compte | {
            "capteur déplacé de plus de 1 km": 1,
            "mesures du capteur déplacé": mesures.height,
        }
    heures = N.moyennes_horaires(mesures, N.cadence_s(mesures))
    compte["mesures gardées"] = mesures.height
    compte["heures avec au moins une mesure"] = mesures["horodatage"].dt.truncate("1h").n_unique()
    compte["heures complètes (≥ 75 %)"] = heures.height
    return heures.with_columns(capteur=pl.lit(paire["capteur"])), compte


def principal(echantillon: bool) -> None:
    depart = time.time()
    racine = P.dossier_donnees(echantillon)
    brut, propre = racine / "brut", racine / "propre"
    propre.mkdir(parents=True, exist_ok=True)
    paires = pl.read_parquet(brut / "paires.parquet")

    print(f"Capteurs : {paires.height} à nettoyer…", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        resultats = list(pool.map(lambda p: nettoyer_capteur(brut, p), paires.iter_rows(named=True)))
    heures = pl.concat([h for h, _ in resultats if h is not None])
    comptes = pl.DataFrame([c for _, c in resultats]).fill_null(0).sum()

    print("Référence EEA (heure UTC = Start − 1 h) et météo DWD…", flush=True)
    eea = pl.concat([pl.read_parquet(f) for f in sorted((brut / "eea").glob("*.parquet"))])
    reference = N.nettoyer_reference(eea)
    meteo = pl.concat([pl.read_parquet(f) for f in sorted((brut / "dwd").glob("*.parquet"))])
    infos = paires.select("capteur", "point", "station", "type_station", "methode", "distance_km", "id_dwd")
    jointes = heures.join(infos, on="capteur")
    avec_reference = jointes.join(reference, on=["point", "heure"])
    completes = avec_reference.join(meteo, on=["id_dwd", "heure"]).drop_nulls(["humidite", "temperature"])
    completes = completes.filter(pl.col("heure").dt.date().is_between(P.DEBUT, P.FIN))

    print("Contrôle de chaque paire (le capteur suit-il sa station ?)…", flush=True)
    controles = pl.DataFrame(
        [
            {"capteur": c, **N.controler_paire(groupe)}
            for (c,), groupe in completes.sort("heure").group_by("capteur")
        ]
    )
    completes = completes.join(controles.select("capteur", "paire_saine"), on="capteur")
    verifier_heures_paires(completes)
    completes.sort("capteur", "heure").write_parquet(propre / "heures_paires.parquet")
    paires.join(controles, on="capteur", how="left").write_parquet(propre / "paires.parquet")

    # L'entonnoir : ce que chaque règle a retiré, dans l'ordre (en mesures, puis en heures et en paires).
    c = {k: int(v[0]) for k, v in comptes.to_dict().items()}
    regles = ["vide", "code d'erreur (négatif)", "plafond du capteur", "PM2.5 > PM10", "plateau 0/0 ≥ 1 h"]
    lignes = [{"etape": "mesures brutes", "unite": "mesures", "nombre": c["mesures brutes"]}]
    lignes += [{"etape": f"retiré : {r}", "unite": "mesures", "nombre": -c[r]} for r in regles]
    lignes += [
        {
            "etape": "retiré : capteur déplacé de plus de 1 km",
            "unite": "mesures",
            "nombre": -c.get("mesures du capteur déplacé", 0),
        },
        {"etape": "mesures gardées", "unite": "mesures", "nombre": c["mesures gardées"]},
        {
            "etape": "heures avec au moins une mesure",
            "unite": "heures",
            "nombre": c["heures avec au moins une mesure"],
        },
        {
            "etape": "retiré : heure incomplète (< 75 %)",
            "unite": "heures",
            "nombre": c["heures complètes (≥ 75 %)"] - c["heures avec au moins une mesure"],
        },
        {
            "etape": "retiré : pas de référence EEA valide",
            "unite": "heures",
            "nombre": avec_reference.height - jointes.height,
        },
        {
            "etape": "retiré : pas de météo DWD ou hors période",
            "unite": "heures",
            "nombre": completes.height - avec_reference.height,
        },
        {"etape": "heures-paires gardées", "unite": "heures", "nombre": completes.height},
        {"etape": "  dont paires saines", "unite": "heures", "nombre": int(completes["paire_saine"].sum())},
        {
            "etape": "capteurs sans aucune mesure",
            "unite": "capteurs",
            "nombre": c.get("capteur sans mesure", 0),
        },
        {
            "etape": "capteurs déplacés de plus de 1 km",
            "unite": "capteurs",
            "nombre": c.get("capteur déplacé de plus de 1 km", 0),
        },
        {"etape": "paires saines", "unite": "paires", "nombre": int(controles["paire_saine"].sum())},
        {"etape": "paires défectueuses", "unite": "paires", "nombre": int((~controles["paire_saine"]).sum())},
    ]
    entonnoir = pl.DataFrame(lignes)
    entonnoir.write_csv(racine / "entonnoir.csv" if echantillon else P.RAPPORTS / "entonnoir.csv")
    print(entonnoir)
    print(f"Terminé en {(time.time() - depart) / 60:.1f} min : {completes.height:,} heures-paires.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--echantillon", action="store_true")
    principal(parser.parse_args().echantillon)
