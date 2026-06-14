"""CLI command group for scheduled and triggered suite validation runs.

Commands
--------
valdo schedule list   — list all configured suites
valdo schedule run    — run a named suite immediately
"""
from __future__ import annotations

import json
import sys

import click

from src.services.scheduler_service import list_suites, run_suite_by_name


@click.group("schedule")
def schedule() -> None:
    """Manage and trigger scheduled validation suite runs."""


@schedule.command("list")
@click.option(
    "--suites-dir",
    default=None,
    show_default=True,
    help="Directory containing YAML suite definitions. Defaults to config/suites/.",
)
@click.option(
    "--json-output",
    "json_output",
    is_flag=True,
    default=False,
    help="Output as machine-readable JSON.",
)
def list_command(suites_dir: str | None, json_output: bool) -> None:
    """List all configured validation suites.

    Reads suite YAML files from the suites directory and prints their names,
    descriptions, and step counts.  Use ``--json-output`` to get structured
    JSON suitable for scripting.

    Args:
        suites_dir: Optional path override for the suites directory.
        json_output: When True, emit JSON instead of human-readable text.
    """
    suites = list_suites(suites_dir=suites_dir)

    if json_output:
        click.echo(json.dumps(suites, indent=2))
        return

    if not suites:
        click.echo("No suites configured. Add YAML files to config/suites/.")
        return

    click.echo(f"{'Name':<40} {'Steps':>6}  Description")
    click.echo("-" * 72)
    for suite in suites:
        desc = suite.get("description") or ""
        click.echo(f"{suite['name']:<40} {suite['step_count']:>6}  {desc}")


@schedule.command("run")
@click.argument("suite_name")
@click.option(
    "--suites-dir",
    default=None,
    show_default=True,
    help="Directory containing YAML suite definitions. Defaults to config/suites/.",
)
@click.option(
    "--json-output",
    "json_output",
    is_flag=True,
    default=False,
    help="Output result as machine-readable JSON.",
)
def run_command(suite_name: str, suites_dir: str | None, json_output: bool) -> None:
    """Run a named validation suite immediately.

    Looks up SUITE_NAME among the YAML files in the suites directory and
    executes every step sequentially.  Exits with code 1 when any step fails
    or the suite is not found.

    Args:
        suite_name: Name of the suite to run (must match the ``name:`` field
            inside the YAML file, not the filename).
        suites_dir: Optional path override for the suites directory.
        json_output: When True, emit the full result dict as JSON.
    """
    result = run_suite_by_name(suite_name, suites_dir=suites_dir)

    if json_output:
        click.echo(json.dumps(result, indent=2))
    else:
        status = result["status"]
        run_id = result.get("run_id", "")
        step_results = result.get("step_results", [])

        if status == "error":
            click.echo(f"ERROR: {result.get('message', 'unknown error')}", err=True)
            sys.exit(1)

        status_icon = "PASSED" if status == "passed" else "FAILED"
        click.echo(f"Suite: {suite_name}  run_id={run_id}  [{status_icon}]")

        for sr in step_results:
            icon = "+" if sr["status"] == "passed" else "-"
            row_info = f"  rows={sr['total_rows']} errors={sr['error_count']}"
            detail = f"  — {sr['detail']}" if sr.get("detail") else ""
            click.echo(f"  [{icon}] {sr['name']}{row_info}{detail}")

    if result["status"] != "passed":
        sys.exit(1)
