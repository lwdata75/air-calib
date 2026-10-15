"""Validate the neighbour health threshold on data it was not chosen on.

For each fold: choose the threshold maximising F1 on the pairs of the 4 other folds
(label: unverifiable pair), measure precision and recall on the held-out fold. Then
apply the threshold chosen on all main-country folds to France, without recalibrating.
"""

from __future__ import annotations

import numpy as np
import polars as pl


def confusion(score: np.ndarray, label: np.ndarray, threshold: float) -> dict[str, float]:
    """Pairs flagged below ``threshold``, true positives, precision, recall and F1 against ``label``."""
    flagged = score < threshold
    tp = int((flagged & label).sum())
    precision = tp / flagged.sum() if flagged.sum() else float("nan")
    recall = tp / label.sum() if label.sum() else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    return {
        "flagged": int(flagged.sum()),
        "true_positives": tp,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def best_threshold(score: np.ndarray, label: np.ndarray, candidates: np.ndarray) -> float:
    """Threshold with the highest F1 (ties: the lower, more cautious threshold)."""
    f1 = [confusion(score, label, t)["f1"] for t in candidates]
    return float(candidates[int(np.argmax(f1))])


def threshold_cv(pairs: pl.DataFrame, candidates: np.ndarray, main_country: str) -> tuple[pl.DataFrame, dict]:
    """Per-fold choice and test of the threshold, then the France check.

    ``pairs``: ``fold, country, sane_pair, health_change_corr`` (pairs with a score).
    """
    scored = pairs.drop_nulls("health_change_corr").with_columns(broken=~pl.col("sane_pair"))
    main = scored.filter(pl.col("country") == main_country)
    rows = []
    for fold in sorted(main["fold"].drop_nulls().unique().to_list()):
        fit_on, test_on = main.filter(pl.col("fold") != fold), main.filter(pl.col("fold") == fold)
        threshold = best_threshold(
            fit_on["health_change_corr"].to_numpy(), fit_on["broken"].to_numpy(), candidates
        )
        rows.append(
            {
                "fold": fold,
                "threshold": threshold,
                "pairs": test_on.height,
                **confusion(
                    test_on["health_change_corr"].to_numpy(), test_on["broken"].to_numpy(), threshold
                ),
            }
        )
    threshold = best_threshold(main["health_change_corr"].to_numpy(), main["broken"].to_numpy(), candidates)
    other = scored.filter(pl.col("country") != main_country)
    transfer = {
        "threshold": threshold,
        "pairs": other.height,
        **confusion(other["health_change_corr"].to_numpy(), other["broken"].to_numpy(), threshold),
    }
    return pl.DataFrame(rows), transfer
