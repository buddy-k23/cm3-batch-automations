"""Unit tests for ``scripts.e2e_lib.jsonl_logger``."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.jsonl_logger import JsonlLogger  # noqa: E402


def _read_lines(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]


class TestEmit:
    def test_writes_one_line_per_event(self, tmp_path: Path) -> None:
        log = JsonlLogger.for_run(
            tmp_path, run_id="r1", env="sit", source="SRC_A"
        )
        log.info("gate_started", gate="L1")
        log.warn("gate_skipped", gate="L2", reason="valdo gap")
        log.error("gate_failed", gate="L3", code=42)
        lines = _read_lines(log.log_path)
        assert [r["event"] for r in lines] == [
            "gate_started", "gate_skipped", "gate_failed",
        ]
        assert [r["level"] for r in lines] == ["INFO", "WARN", "ERROR"]
        assert all(r["run_id"] == "r1" for r in lines)
        assert all(r["env"] == "sit" for r in lines)
        assert all(r["source"] == "SRC_A" for r in lines)

    def test_filename_includes_run_id_and_source(self, tmp_path: Path) -> None:
        log = JsonlLogger.for_run(
            tmp_path, run_id="20260513_120000", env="sit", source="SRC_A"
        )
        log.info("x")
        assert log.log_path.name == "e2e_20260513_120000_SRC_A.jsonl"

    def test_reserved_fields_cannot_be_spoofed(self, tmp_path: Path) -> None:
        log = JsonlLogger.for_run(
            tmp_path, run_id="r", env="sit", source="S"
        )
        # Reserved keys passed via the **fields kwargs of emit() must be
        # silently dropped — the logger stamps run_id/env/source from its
        # own identity, never from caller-supplied fields.
        log.emit(
            "x", level="INFO",
            **{
                "run_id": "HACKED",
                "env": "HACKED",
                "source": "HACKED",
                "ts": "HACKED",
                # ``event`` cannot be spoofed because it is a positional
                # parameter — Python rejects the duplicate before this
                # function body ever runs. That is the API guarantee.
            },
        )
        record = _read_lines(log.log_path)[0]
        assert record["run_id"] == "r"
        assert record["env"] == "sit"
        assert record["source"] == "S"
        assert record["event"] == "x"
        assert record["level"] == "INFO"
        assert record["ts"] != "HACKED"

    def test_unknown_level_coerced_to_info(self, tmp_path: Path) -> None:
        log = JsonlLogger.for_run(tmp_path, run_id="r", env="sit", source="S")
        log.emit("x", level="CRITICAL")
        assert _read_lines(log.log_path)[0]["level"] == "INFO"

    def test_unserializable_payload_falls_back(self, tmp_path: Path) -> None:
        log = JsonlLogger.for_run(tmp_path, run_id="r", env="sit", source="S")
        circular: list = []
        circular.append(circular)
        log.emit("x", data=circular)
        records = _read_lines(log.log_path)
        # Either the line was dropped to avoid a crash, or a fallback marker
        # was written. The point is the call did not raise and any line
        # written is valid JSON.
        for r in records:
            assert "event" in r and "run_id" in r

    def test_emit_never_raises_on_filesystem_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = JsonlLogger.for_run(tmp_path, run_id="r", env="sit", source="S")

        def boom(self, *a, **kw):  # noqa: ARG001
            raise OSError("disk full")

        monkeypatch.setattr(Path, "open", boom)
        # Must NOT raise.
        log.info("x")

    def test_appends_across_calls(self, tmp_path: Path) -> None:
        log = JsonlLogger.for_run(tmp_path, run_id="r", env="sit", source="S")
        for i in range(5):
            log.info("evt", i=i)
        assert len(_read_lines(log.log_path)) == 5

    def test_concurrent_emits_produce_complete_lines(
        self, tmp_path: Path
    ) -> None:
        log = JsonlLogger.for_run(tmp_path, run_id="r", env="sit", source="S")

        def worker(n: int) -> None:
            for i in range(50):
                log.info("evt", worker=n, i=i)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        lines = _read_lines(log.log_path)
        assert len(lines) == 200  # 4 threads * 50 events; no torn lines
        assert all("event" in r and "worker" in r for r in lines)
