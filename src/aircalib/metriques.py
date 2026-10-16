"""Les métriques : erreur globale, et cibles de l'EPA paire par paire.

Convention de l'EPA : on régresse la prédiction (ou le capteur brut) sur la référence,
prediction = pente × référence + ordonnée. Une paire « passe » si elle tient les quatre cibles :
R² ≥ 0,70, pente entre 0,65 et 1,35, ordonnée entre −5 et +5 µg/m³, RMSE ≤ 7 µg/m³.
"""

import polars as pl

from aircalib import parametres as P

X, Y = pl.col("reference"), pl.col("prediction")


def stats_regression(table: pl.DataFrame, par: list[str]) -> pl.DataFrame:
    """Par groupe : nombre de lignes, RMSE, MAE, R², pente et ordonnée de la prédiction sur la référence."""
    pente = pl.cov(X, Y) / X.var()
    return (
        table.group_by(par)
        .agg(
            n=pl.len(),
            rmse=((Y - X) ** 2).mean().sqrt(),
            mae=(Y - X).abs().mean(),
            r2=pl.corr(X, Y) ** 2,
            pente=pente,
            ordonnee=Y.mean() - pente * X.mean(),
        )
        .with_columns(
            passe_epa=(pl.col("r2") >= P.EPA_R2_MIN)
            & pl.col("pente").is_between(*P.EPA_PENTE)
            & pl.col("ordonnee").is_between(*P.EPA_ORDONNEE)
            & (pl.col("rmse") <= P.EPA_RMSE_MAX)
        )
    )


def moyennes_journalieres(table: pl.DataFrame) -> pl.DataFrame:
    """Moyennes par paire et par jour, pour les jours qui ont au moins 18 heures (75 % de la journée)."""
    return (
        table.group_by("capteur", jour=pl.col("heure").dt.date())
        .agg(pl.col("reference").mean(), pl.col("prediction").mean(), heures=pl.len())
        .filter(pl.col("heures") >= P.HEURES_MIN_JOUR)
    )


def resume(table: pl.DataFrame) -> dict[str, float]:
    """Les chiffres qu'on suit pour un modèle : erreur globale, R² médian par paire, % de paires EPA.

    `table` contient une ligne par heure-paire avec `capteur`, `heure`, `reference` et `prediction`.
    Une paire n'est notée que si elle a assez de données (500 heures, ou 30 jours complets).
    """
    if table.is_empty():
        return {"heures": 0} | dict.fromkeys(
            [
                "rmse",
                "mae",
                "r2_global",
                "pente_globale",
                "paires_notees",
                "r2_median_par_paire",
                "pente_mediane_par_paire",
                "pct_epa_horaire",
                "pct_epa_journalier",
            ],
            float("nan"),
        )
    globale = stats_regression(table.with_columns(tout=pl.lit(1)), ["tout"]).row(0, named=True)
    horaire = stats_regression(table, ["capteur"]).filter(pl.col("n") >= P.HEURES_MIN_PAIRE)
    journalier = stats_regression(moyennes_journalieres(table), ["capteur"]).filter(
        pl.col("n") >= P.JOURS_MIN_PAIRE
    )
    return {
        "heures": table.height,
        "rmse": globale["rmse"],
        "mae": globale["mae"],
        "r2_global": globale["r2"],
        "pente_globale": globale["pente"],
        "paires_notees": horaire.height,
        "r2_median_par_paire": horaire["r2"].median(),
        "pente_mediane_par_paire": horaire["pente"].median(),
        "pct_epa_horaire": 100 * horaire["passe_epa"].mean() if horaire.height else float("nan"),
        "pct_epa_journalier": 100 * journalier["passe_epa"].mean() if journalier.height else float("nan"),
    }


def resume_par(table: pl.DataFrame, colonne: str) -> pl.DataFrame:
    """Le même résumé, tranche par tranche (avec / sans voisins, bande de distance…)."""
    lignes = [{colonne: valeur, **resume(groupe)} for (valeur,), groupe in table.group_by(colonne)]
    return pl.DataFrame(lignes).sort(colonne)
