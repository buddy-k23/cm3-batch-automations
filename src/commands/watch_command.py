"""Watch a directory for batch completion trigger files and run the matching test suite."""

import time
import re
from pathlib import Path
from typing import Optional
from src.utils.logger import setup_logger


def _extract_run_date_from_trigger(trigger_path: Path) -> Optional[str]:
    """Extract run date from trigger filename.

    Expects filename pattern: batch_complete_YYYYMMDD.trigger

    Args:
        trigger_path: Path to the trigger file.

    Returns:
        Date string like "20260301", or None if pattern not matched.
    """
    match = re.search(r'(\d{8})', trigger_path.stem)
    return match.group(1) if match else None


def _find_matching_suite(suites_dir: Path, run_date: Optional[str]) -> Optional[Path]:
    """Find the best matching suite YAML for the given run date.

    Looks for a YAML containing the run_date in its filename first,
    then falls back to the first available .yaml file.

    Args:
        suites_dir: Directory containing suite YAML files.
        run_date: Date string like "20260301", or None.

    Returns:
        Path to the matched YAML file, or None if no suites found.
    """
    yamls = sorted(suites_dir.glob("*.yaml"))
    if not yamls:
        return None
    if run_date:
        for y in yamls:
            if run_date in y.name:
                return y
    return yamls[0]


def watch_once(watch_dir: Path, suites_dir: Path, env: str, output_dir: Path, logger) -> int:
    """Check for trigger files and run matching suites.

    Scans ``watch_dir`` for ``*.trigger`` files, maps each to a suite YAML via
    :func:`_find_matching_suite`, delegates execution to
    :func:`src.commands.run_tests_command.run_suite_from_path`, and removes the
    trigger file whether or not a suite was found.

    Args:
        watch_dir: Directory to scan for trigger files.
        suites_dir: Directory containing suite YAML files.
        env: Environment name passed through to the suite runner.
        output_dir: Directory for test reports.
        logger: Logger instance for info/warning messages.

    Returns:
        Number of suites actually run (trigger files with a matching suite).
    """
    triggers = list(watch_dir.glob("*.trigger"))
    ran = 0
    for trigger in triggers:
        run_date = _extract_run_date_from_trigger(trigger)
        suite_path = _find_matching_suite(suites_dir, run_date)
        if suite_path is None:
            logger.warning(f"No suite found for trigger {trigger.name}, skipping")
            trigger.unlink()
            continue
        logger.info(f"Trigger {trigger.name} matched suite {suite_path.name}")
        params = {}
        if run_date:
            params["run_date"] = run_date
        # Import here to avoid circular imports
        from src.commands.run_tests_command import run_suite_from_path
        run_suite_from_path(str(suite_path), params=params, env=env, output_dir=str(output_dir))
        trigger.unlink()
        ran += 1
    return ran


def run_watch(
    watch_dir: str,
    suites_dir: str,
    env: str,
    output_dir: str,
    poll_interval: int = 30,
    max_iterations: Optional[int] = None,
) -> None:
    """Poll watch_dir for .trigger files and run matching test suites.

    Runs indefinitely (or until ``max_iterations`` is reached) by calling
    :func:`watch_once` on each poll cycle, then sleeping for ``poll_interval``
    seconds.

    Args:
        watch_dir: Directory to watch for trigger files.
        suites_dir: Directory containing suite YAML files.
        env: Environment name (dev/staging/prod).
        output_dir: Directory for test reports.
        poll_interval: Seconds between polls. Defaults to 30.
        max_iterations: Stop after N iterations (for testing). None = run forever.
    """
    logger = setup_logger("cm3-watch", log_to_file=False)
    watch_path = Path(watch_dir)
    suites_path = Path(suites_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info(f"Watching {watch_path} every {poll_interval}s for .trigger files")
    iterations = 0
    while True:
        watch_once(watch_path, suites_path, env, output_path, logger)
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        time.sleep(poll_interval)
