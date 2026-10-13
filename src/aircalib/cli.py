"""Command-line interface: ``python -m aircalib <command>``.

Commands are added stage by stage (see docs/PLAN.md); ``all`` runs every
stage implemented so far, in order.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Annotated

import polars as pl
import typer

from aircalib.config import get_settings
from aircalib.ingest import discovery
from aircalib.logging import console, setup_logging

app = typer.Typer(help="Calibrate low-cost PM sensors against EEA reference stations.", no_args_is_help=True)

SampleOpt = Annotated[bool, typer.Option("--sample", help="Fast iteration on a small subset.")]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")]


@app.callback()
def _main(verbose: VerboseOpt = False) -> None:
    setup_logging("DEBUG" if verbose else "INFO")


@app.command()
def discover(sample: SampleOpt = False) -> None:
    """Step 1: inventory sensors and stations, count candidate pairs, check clocks."""
    if sample:
        console.print("[yellow]--sample has no effect on discovery (metadata only).[/]")
    results = asyncio.run(discovery.run(get_settings(), today=date.today()))
    with pl.Config(tbl_rows=40, tbl_cols=12):
        console.print(results["summary"])


@app.command(name="all")
def run_all(sample: SampleOpt = False) -> None:
    """Run every implemented stage in order."""
    discover(sample=sample)


@app.command()
def config() -> None:
    """Print the effective configuration."""
    console.print_json(get_settings().model_dump_json())
