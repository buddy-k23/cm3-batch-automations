"""Trigger-file watcher for the E2E batch testing harness (M7).

This is the Python core invoked by ``scripts/valdo_watch_wrapper.sh``.

What it does
------------
Polls every ``--poll-interval`` seconds (default: 5) every configured
trigger directory under ``<trigger_root>`` and processes any ``.trigger``
sidecar file it finds.

A trigger sidecar carries the basename of the data file it announces
(``paths.yml`` ``trigger_file.data_file_line`` controls which 1-indexed
line — default 1). The watcher:

  1. Reads the sidecar's data filename.
  2. Classifies it through :class:`PathResolver.classify_filename` to get
     ``(source, file_type, direction)``.
  3. Within one poll cycle, **deduplicates** the routed work by source so
     that ten input-trigger files for SRC_A produce one
     ``run_e2e_source.sh`` invocation, not ten.
  4. Invokes ``scripts/run_e2e_source.sh`` per resolved source. The
     full per-source pipeline is run; M4's wrapper internally decides
     which gates to skip when a phase's prerequisites are absent.
  5. Renames the trigger sidecar to ``<name>.trigger.processed`` so it
     is never re-processed. Files that fail to classify are renamed to
     ``<name>.trigger.unmatched`` and logged at WARN level.

Latency
-------
With the default 5-second poll interval the worst-case end-to-end
latency from sidecar drop to ``run_e2e_source.sh`` start is
``poll_interval`` seconds, which comfortably meets the prompt's
``<=10 seconds`` acceptance criterion #5.

Daemonization
-------------
The watcher itself is a foreground process; daemonization is the
operator's responsibility (systemd unit or CA ESP job). The bash
wrapper installs a SIGTERM/SIGINT trap that exits the polling loop
cleanly between cycles.

CLI
---
::

    python -m scripts.e2e_lib.watch \\
        --env sit \\
        [--poll-interval 5]
        [--paths-yaml config/e2e/paths.yml]
        [--sources-dir config/e2e/sources]
        [--watch-script scripts/run_e2e_source.sh]
        [--once]                                # one cycle and exit (testing)
        [--max-iterations N]                    # cap iterations (testing)
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Make the repo importable when invoked as a script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.jsonl_logger import JsonlLogger  # noqa: E402
from scripts.e2e_lib.path_resolver import (  # noqa: E402
    MatchedFile,
    PathResolver,
    PathResolverError,
)

EXIT_OK = 0
EXIT_INFRA_ERROR = 3

# Default poll cadence. Faster than Valdo's stock 30s `watch` so the
# acceptance criterion (<=10s end-to-end) is met with headroom.
_DEFAULT_POLL_INTERVAL = 5

# Subprocess-runner factory shape used by tests to inject a stub.
SubprocessRunner = Callable[[List[str]], "subprocess.CompletedProcess[str]"]


class WatchError(RuntimeError):
    """Raised for any unrecoverable watcher configuration problem."""


# --------------------------------------------------------------------------- #
# Trigger discovery + classification
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TriggerEvent:
    """One observed trigger sidecar that classified successfully.

    ``source`` is the value of the regex's ``source`` named group, which is
    informational. ``owning_source`` (R-07) is the harness source that owns the
    trigger directory the sidecar was found in; it is the value the watcher
    routes the run to. They coincide for the global CA-ESP patterns (whose
    ``source`` group equals the directory source), but a per-source override
    pattern may capture a producer-specific token that differs from the owning
    source. ``owning_source`` falls back to ``source`` when the owner is unknown
    (e.g. ``classify_triggers`` was called without a ``source``).
    """

    trigger_path: Path
    data_filename: str
    source: str
    file_type: str
    direction: str
    pattern_name: str
    owning_source: str = ""

    @property
    def run_source(self) -> str:
        """The source to route the run to (owner if known, else regex group)."""
        return self.owning_source or self.source


@dataclass(frozen=True)
class UnmatchedTrigger:
    """One observed sidecar that could not be classified.

    ``reason`` is a short machine-friendly token used for JSONL logging
    and for the renamed sidecar's suffix.
    """

    trigger_path: Path
    data_filename: Optional[str]
    reason: str


def _read_data_filename(trigger_path: Path, line_number_1based: int) -> Optional[str]:
    """Return the basename recorded inside ``trigger_path``.

    The watcher reads only the first ``line_number_1based`` lines so a
    misconfigured sidecar with megabytes of garbage cannot OOM the
    process.

    Returns:
        The stripped value of the configured line, or ``None`` if the
        line is absent / blank.
    """
    try:
        with trigger_path.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, start=1):
                if i == line_number_1based:
                    candidate = line.strip()
                    return candidate or None
                if i > line_number_1based:
                    break
    except OSError:
        return None
    return None


def classify_triggers(
    trigger_files: Iterable[Path],
    *,
    resolver: PathResolver,
    data_file_line: int = 1,
    source: Optional[str] = None,
) -> Tuple[List[TriggerEvent], List[UnmatchedTrigger]]:
    """Classify every sidecar into matched/unmatched lists.

    Args:
        trigger_files: Sidecar paths to inspect. The function does not
            list directories; the caller is responsible for ``glob``-ing.
        resolver: A ready :class:`PathResolver` — used only for its
            ``classify_filename`` method.
        data_file_line: Which 1-indexed line in the sidecar carries the
            data filename (controlled by ``paths.yml`` ``trigger_file.data_file_line``).
        source: Optional owning source for these sidecars. When given, the
            source's per-source ``filename_patterns`` override (R-07) is used
            for classification; otherwise the global patterns apply.

    Returns:
        ``(matched, unmatched)``. Both lists preserve input order so the
        caller's logs read consistently.
    """
    matched: List[TriggerEvent] = []
    unmatched: List[UnmatchedTrigger] = []

    for trigger in trigger_files:
        data_filename = _read_data_filename(trigger, data_file_line)
        if not data_filename:
            unmatched.append(
                UnmatchedTrigger(
                    trigger_path=trigger,
                    data_filename=None,
                    reason="empty_or_unreadable",
                )
            )
            continue

        # Defensive: only the basename is meaningful for classification.
        candidate = Path(data_filename).name
        result: Optional[MatchedFile] = resolver.classify_filename(
            candidate, source=source
        )
        if result is None:
            unmatched.append(
                UnmatchedTrigger(
                    trigger_path=trigger,
                    data_filename=candidate,
                    reason="no_pattern_matched",
                )
            )
            continue

        if result.direction not in ("input", "output"):
            unmatched.append(
                UnmatchedTrigger(
                    trigger_path=trigger,
                    data_filename=candidate,
                    reason=f"invalid_direction:{result.direction}",
                )
            )
            continue

        matched.append(
            TriggerEvent(
                trigger_path=trigger,
                data_filename=candidate,
                source=result.source,
                file_type=result.file_type,
                direction=result.direction,
                pattern_name=result.pattern_name,
                owning_source=source or "",
            )
        )
    return matched, unmatched


# --------------------------------------------------------------------------- #
# Routing & sidecar lifecycle
# --------------------------------------------------------------------------- #


def deduplicate_by_source(
    events: Sequence[TriggerEvent],
) -> List[str]:
    """Collapse one cycle's events to the ordered, unique run-source list.

    Routes by :attr:`TriggerEvent.run_source` (the owning harness source when
    known, else the regex ``source`` group) so a per-source override pattern
    (R-07) that captures a producer-specific token still runs the correct
    harness source.
    """
    seen: Set[str] = set()
    out: List[str] = []
    for ev in events:
        run_source = ev.run_source
        if run_source in seen:
            continue
        seen.add(run_source)
        out.append(run_source)
    return out


def mark_processed(trigger_path: Path) -> Path:
    """Rename ``foo.trigger`` to ``foo.trigger.processed``.

    Renames are atomic on every POSIX filesystem and on NTFS, so the
    "already processed" marker is safe even when the watcher races a
    CA ESP drop. Idempotent: an already-renamed sidecar is left alone.
    """
    if trigger_path.name.endswith(".processed"):
        return trigger_path
    target = trigger_path.with_suffix(trigger_path.suffix + ".processed")
    if target.exists():
        # Preserve history: append a UTC-stamped suffix so a rerun never
        # silently overwrites the prior record.
        target = trigger_path.with_suffix(
            trigger_path.suffix + ".processed." + _utc_stamp()
        )
    os.replace(trigger_path, target)
    return target


def mark_unmatched(trigger_path: Path, reason: str) -> Path:
    """Rename ``foo.trigger`` to ``foo.trigger.unmatched`` (with reason)."""
    target = trigger_path.with_suffix(trigger_path.suffix + ".unmatched")
    if target.exists():
        target = trigger_path.with_suffix(
            trigger_path.suffix + ".unmatched." + _utc_stamp()
        )
    os.replace(trigger_path, target)
    # The reason lives next to the file as a sidecar-of-the-sidecar so an
    # operator can grep without re-running classification.
    reason_file = target.with_suffix(target.suffix + ".reason")
    try:
        reason_file.write_text(reason + "\n", encoding="utf-8")
    except OSError:
        pass
    return target


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------- #
# Polling loop
# --------------------------------------------------------------------------- #


def _default_runner(cmd: List[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=True,
    )


@dataclass
class Watcher:
    """Per-env trigger watcher; intended to be constructed once and reused."""

    env: str
    resolver: PathResolver
    watch_script: Path
    paths_yaml: Path
    sources_dir: Path
    valdo_executable: str = "valdo"
    poll_interval: int = _DEFAULT_POLL_INTERVAL
    trigger_suffix: str = ".trigger"
    data_file_line: int = 1
    subprocess_runner: SubprocessRunner = field(default=_default_runner)
    # Set to True (via signal handler) to drop out of the polling loop.
    _stop: bool = field(default=False, init=False)
    _logger: Optional[JsonlLogger] = field(default=None, init=False)

    # ----- factories ------------------------------------------------- #

    @classmethod
    def from_paths(
        cls,
        *,
        env: str,
        paths_yaml: Path,
        sources_dir: Path,
        watch_script: Path,
        valdo_executable: str = "valdo",
        poll_interval: int = _DEFAULT_POLL_INTERVAL,
        subprocess_runner: Optional[SubprocessRunner] = None,
    ) -> "Watcher":
        try:
            resolver = PathResolver.from_files(paths_yaml, sources_dir)
        except PathResolverError as exc:
            raise WatchError(str(exc)) from exc
        if env not in resolver.known_envs():
            raise WatchError(
                f"unknown env={env!r}; known envs: {resolver.known_envs()}"
            )
        # Global trigger defaults (R-07): these are the fallback used when a
        # source declares no per-source ``trigger_file`` override. Per-source
        # values are resolved inside ``poll_once`` via
        # ``resolver.trigger_config(source)``.
        try:
            trigger_cfg = resolver.trigger_config()
        except PathResolverError as exc:
            raise WatchError(str(exc)) from exc
        return cls(
            env=env,
            resolver=resolver,
            watch_script=Path(watch_script),
            paths_yaml=Path(paths_yaml),
            sources_dir=Path(sources_dir),
            valdo_executable=valdo_executable,
            poll_interval=int(poll_interval),
            trigger_suffix=str(trigger_cfg["suffix"]),
            data_file_line=int(trigger_cfg["data_file_line"]),
            subprocess_runner=subprocess_runner or _default_runner,
        )

    # ----- main loop ------------------------------------------------- #

    def request_stop(self) -> None:
        """Signal the polling loop to exit between cycles."""
        self._stop = True

    def install_signal_handlers(self) -> None:
        """Wire SIGTERM/SIGINT to :meth:`request_stop`.

        Idempotent across calls. Failures (e.g. on Windows where SIGTERM
        is not delivered the same way) are silently ignored — the watcher
        still exits cleanly when its loop ticks past ``--max-iterations``.
        """
        for sig_name in ("SIGTERM", "SIGINT"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, lambda *_: self.request_stop())
            except (ValueError, OSError):
                # Not the main thread, or the signal can't be installed.
                pass

    def run_forever(self, *, max_iterations: Optional[int] = None) -> int:
        """Poll until SIGTERM/SIGINT or ``max_iterations`` is reached."""
        iterations = 0
        while not self._stop:
            self.poll_once()
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            # Sleep in small slices so a Ctrl-C is responsive.
            slept = 0
            while slept < self.poll_interval and not self._stop:
                step = min(1, self.poll_interval - slept)
                time.sleep(step)
                slept += step
        return EXIT_OK

    def poll_once(self) -> Dict[str, Any]:
        """One poll cycle. Returns a small summary useful for tests/logs."""
        # Per-source trigger discovery (R-07): each source's trigger directory
        # is globbed with that source's own ``suffix`` and its sidecars are
        # classified against that source's ``filename_patterns`` (falling back
        # to the global defaults when the source declares no override). This
        # keeps every existing source byte-identical while letting a non-CA-ESP
        # producer own its trigger naming and grammar.
        sidecars: List[Path] = []
        matched: List[TriggerEvent] = []
        unmatched: List[UnmatchedTrigger] = []
        for source, d in self._trigger_dirs_by_source():
            if not d.is_dir():
                continue
            try:
                trig = self.resolver.trigger_config(source)
            except PathResolverError:
                # Fall back to the watcher-level defaults if the source's
                # override is malformed; the defect is surfaced at load time
                # for the global block, so this only guards a bad overlay.
                trig = {
                    "suffix": self.trigger_suffix,
                    "data_file_line": self.data_file_line,
                }
            suffix = str(trig["suffix"])
            data_file_line = int(trig["data_file_line"])

            dir_sidecars: List[Path] = []
            for p in sorted(d.glob("*" + suffix)):
                # Skip already-processed sidecars and stray reason files.
                if p.suffix != suffix:
                    continue
                dir_sidecars.append(p)
            sidecars.extend(dir_sidecars)

            dir_matched, dir_unmatched = classify_triggers(
                dir_sidecars,
                resolver=self.resolver,
                data_file_line=data_file_line,
                source=source,
            )
            matched.extend(dir_matched)
            unmatched.extend(dir_unmatched)

        sources = deduplicate_by_source(matched)
        results: List[Dict[str, Any]] = []
        for source in sources:
            run_id = _utc_stamp()
            cmd = [
                "bash",
                str(self.watch_script),
                "--env",
                self.env,
                "--source",
                source,
                "--run-id",
                run_id,
                "--paths-yaml",
                str(self.paths_yaml),
                "--sources-dir",
                str(self.sources_dir),
                "--valdo-executable",
                self.valdo_executable,
            ]
            self._log("source_run_started", source=source, run_id=run_id)
            cp = self.subprocess_runner(cmd)
            results.append(
                {
                    "source": source,
                    "run_id": run_id,
                    "returncode": cp.returncode,
                    "stderr_tail": (cp.stderr or "")[-512:],
                }
            )
            self._log(
                "source_run_finished",
                level="INFO" if cp.returncode == 0 else "ERROR",
                source=source,
                run_id=run_id,
                returncode=cp.returncode,
            )

        # Rename sidecars only AFTER the per-source runs return. If the
        # watcher dies mid-cycle, the sidecars remain in place and the
        # next poll re-processes them — at-least-once semantics.
        for ev in matched:
            try:
                mark_processed(ev.trigger_path)
            except OSError as exc:
                self._log(
                    "trigger_rename_failed",
                    level="ERROR",
                    trigger=str(ev.trigger_path),
                    error=str(exc),
                )
        for u in unmatched:
            self._log(
                "trigger_unmatched",
                level="WARN",
                trigger=str(u.trigger_path),
                data_filename=u.data_filename,
                reason=u.reason,
            )
            try:
                mark_unmatched(u.trigger_path, u.reason)
            except OSError as exc:
                self._log(
                    "trigger_rename_failed",
                    level="ERROR",
                    trigger=str(u.trigger_path),
                    error=str(exc),
                )

        return {
            "sidecars_seen": len(sidecars),
            "matched": len(matched),
            "unmatched": len(unmatched),
            "sources_run": sources,
            "results": results,
        }

    # ----- internals ------------------------------------------------- #

    def _trigger_dirs_by_source(self) -> List[Tuple[str, Path]]:
        """Return every ``(source, trigger_dir)`` pair the watcher should poll.

        ``paths.yml`` declares ``trigger_root`` as a templated path carrying
        ``{source}``. We resolve it for every known source so the watcher only
        polls directories the harness actually expects, and we keep the source
        alongside the directory so the caller can apply that source's
        per-source trigger naming and ``filename_patterns`` (R-07).
        """
        out: List[Tuple[str, Path]] = []
        sources_dir = Path(self.sources_dir)
        if not sources_dir.is_dir():
            return out
        for p in sorted(sources_dir.glob("*.yml")):
            source = p.stem
            try:
                resolved = self.resolver.resolve(
                    "trigger_root",
                    env=self.env,
                    source=source,
                )
            except PathResolverError:
                continue
            out.append((source, Path(resolved)))
        return out

    def _log(self, event: str, *, level: str = "INFO", **fields: Any) -> None:
        """Emit a structured event to a per-day JSONL under <log_root>.

        Lazy-creates the logger on first use so a startup-time failure
        does not crash the watcher.
        """
        if self._logger is None:
            try:
                log_root = Path(self.resolver.resolve("log_root", env=self.env))
            except PathResolverError:
                return
            day = _utc_stamp()[:8]  # YYYYMMDD
            self._logger = JsonlLogger(
                log_path=log_root / f"e2e_watch_{self.env}_{day}.jsonl",
                run_id="watch",
                env=self.env,
                source="<watch>",
            )
        self._logger.emit(event, level=level, **fields)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="valdo_e2e_watch",
        description=(
            "Trigger-file watcher for the Valdo E2E batch testing harness. "
            "Polls every source's trigger_root and routes .trigger sidecars "
            "to scripts/run_e2e_source.sh."
        ),
    )
    p.add_argument("--env", required=True)
    p.add_argument(
        "--paths-yaml",
        default="config/e2e/paths.yml",
        type=Path,
    )
    p.add_argument(
        "--sources-dir",
        default="config/e2e/sources",
        type=Path,
    )
    p.add_argument(
        "--watch-script",
        default="scripts/run_e2e_source.sh",
        type=Path,
        help="Path to run_e2e_source.sh (the per-source wrapper).",
    )
    p.add_argument(
        "--poll-interval",
        default=_DEFAULT_POLL_INTERVAL,
        type=int,
        help=f"Seconds between polls (default: {_DEFAULT_POLL_INTERVAL}).",
    )
    p.add_argument("--valdo-executable", default="valdo")
    p.add_argument(
        "--once",
        action="store_true",
        help="Run one poll cycle and exit (useful for testing / cron).",
    )
    p.add_argument(
        "--max-iterations",
        default=None,
        type=int,
        help="Stop after N poll cycles (useful for testing).",
    )
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        watcher = Watcher.from_paths(
            env=args.env,
            paths_yaml=args.paths_yaml,
            sources_dir=args.sources_dir,
            watch_script=args.watch_script,
            valdo_executable=args.valdo_executable,
            poll_interval=args.poll_interval,
        )
    except WatchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INFRA_ERROR

    watcher.install_signal_handlers()
    if args.once:
        watcher.poll_once()
        return EXIT_OK
    return watcher.run_forever(max_iterations=args.max_iterations)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
