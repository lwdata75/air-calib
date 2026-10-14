"""Command-line interface: ``python -m aircalib <command> [--sample]``.

Commands are added stage by stage (see docs/PLAN.md); ``all`` runs every
stage implemented so far, in order. ``--sample`` runs the same code on a small
subset written under ``data/sample/``.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Annotated

import polars as pl
import typer

from aircalib.config import Settings, get_settings
from aircalib.gold import build as gold_stage
from aircalib.ingest import bronze as bronze_stage
from aircalib.ingest import discovery, plan, report
from aircalib.ingest import download as download_stage
from aircalib.logging import console, setup_logging
from aircalib.silver import pairs as pair_stage
from aircalib.silver import report as silver_report
from aircalib.silver import sources as silver_stage

app = typer.Typer(help="Calibrate low-cost PM sensors against EEA reference stations.", no_args_is_help=True)

SampleOpt = Annotated[bool, typer.Option("--sample", help="Fast iteration on a small subset.")]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")]


def _settings(sample: bool) -> Settings:
    settings = get_settings()
    return settings.for_sample() if sample else settings


@app.callback()
def _main(verbose: VerboseOpt = False) -> None:
    setup_logging("DEBUG" if verbose else "INFO")


@app.command()
def discover(sample: SampleOpt = False) -> None:
    """Step 1: inventory sensors and stations, count candidate pairs, check clocks."""
    if sample:
        console.print("[yellow]--sample has no effect on discovery (shared reference tables).[/]")
    results = asyncio.run(discovery.run(get_settings(), today=date.today()))
    with pl.Config(tbl_rows=40, tbl_cols=12):
        console.print(results["summary"])


@app.command()
def download(sample: SampleOpt = False) -> None:
    """Step 2a: build the plan and fetch every raw file it needs (resumable)."""
    settings = _settings(sample)
    the_plan = plan.build_plan(settings)
    failures = asyncio.run(download_stage.run(settings, the_plan, today=date.today()))
    console.print(f"download failures per source: {failures}")
    console.print(f"report: {report.write(settings)}")


@app.command()
def bronze(sample: SampleOpt = False) -> None:
    """Step 2b: convert raw files to validated Parquet and delete the converted raw files."""
    settings = _settings(sample)
    failures = bronze_stage.run(settings, plan.load_plan(settings))
    console.print(f"bronze failures per source: {failures}")
    console.print(f"report: {report.write(settings)}")


@app.command()
def silver(sample: SampleOpt = False) -> None:
    """Step 3a: segments and their stations, then a status per reading and hour, hourly aggregation."""
    settings = _settings(sample)
    silver_stage.run(settings, plan.load_plan(settings))


@app.command()
def pair(sample: SampleOpt = False) -> None:
    """Step 3b: join every pair-hour with its sources, check clocks, write the funnel report."""
    settings = _settings(sample)
    pair_stage.build(settings)
    console.print(f"funnel: {silver_report.write(settings)}")


@app.command()
def features(sample: SampleOpt = False) -> None:
    """Step 5: gold layer, usable pair-hours with features, station folds and the physical model."""
    gold_stage.build(_settings(sample))


@app.command()
def train(
    study: Annotated[
        str, typer.Option(help="ladder, ablation, scale, monotone, unverifiable, x, optuna, final")
    ],
    sample: SampleOpt = False,
) -> None:
    """Step 6: train and cross-validate models by station, tracked in MLflow (``sqlite:///mlflow.db``)."""
    from aircalib.models.runtime import ensure_openmp  # noqa: PLC0415 - may re-launch the command (macOS)

    ensure_openmp()
    from aircalib.models import studies  # noqa: PLC0415 - imports LightGBM

    studies.run(_settings(sample), study)


@app.command(name="all")
def run_all(sample: SampleOpt = False) -> None:
    """Run every implemented stage in order."""
    discover(sample=sample)
    download(sample=sample)
    bronze(sample=sample)
    silver(sample=sample)
    pair(sample=sample)
    features(sample=sample)


@app.command()
def config() -> None:
    """Print the effective configuration."""
    console.print_json(get_settings().model_dump_json())
