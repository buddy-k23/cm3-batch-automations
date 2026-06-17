"""Prove ``db-compare`` emits a real HTML report (S23-2, #444).

Before this story the ``output_format=html`` flag on
:func:`~src.services.db_file_compare_service.compare_db_to_file` was accepted
but "informational only" — no HTML was ever rendered, and the CLI always wrote
JSON regardless of the requested format or output extension.

These tests drive the fix test-first against a **real on-disk SQLite database**
(created under pytest's ``tmp_path`` — never a tracked fixture DB).  They assert
that:

* the service renders a genuine ``.html`` file (not a no-op) via the existing
  :class:`~src.reports.renderers.comparison_renderer.HTMLReporter`, reusing the
  file-compare renderer because the db-compare ``compare`` section is the exact
  ``run_compare_service`` output shape the renderer already consumes;
* the rendered HTML contains the comparison verdict (a seeded diff value);
* the consistent output contract holds — ``.json`` still writes machine JSON;
* the CLI ``valdo db-compare --output report.html`` writes HTML.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("API_KEYS", "test-key:admin")

from src.commands.db_compare import run_db_compare_command
from src.services.db_file_compare_service import compare_db_to_file


# ---------------------------------------------------------------------------
# Fixtures — a real SQLite DB + a matching/diffing flat file, both tmp_path
# ---------------------------------------------------------------------------

def _seed_db(tmp_path):
    db_path = tmp_path / "db_compare_html.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE ACCOUNTS (ID INTEGER, NAME TEXT, BALANCE TEXT)")
    conn.executemany(
        "INSERT INTO ACCOUNTS VALUES (?, ?, ?)",
        [(1, "alice", "100"), (2, "bob", "200"), (3, "carol", "300")],
    )
    conn.commit()
    conn.close()
    return str(db_path)


def _mapping():
    return {
        "name": "accounts",
        "fields": [{"name": "ID"}, {"name": "NAME"}, {"name": "BALANCE"}],
    }


def _write_file(path, rows):
    lines = ["ID|NAME|BALANCE"]
    lines.extend("|".join(str(c) for c in r) for r in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _override(db_path):
    return {"db_adapter": "sqlite", "db_path": db_path}


# ---------------------------------------------------------------------------
# Service layer — output_format=html renders a real HTML report
# ---------------------------------------------------------------------------

class TestServiceHtmlReport:
    def test_html_output_writes_real_file(self, tmp_path):
        """``output_format=html`` + ``output_path`` writes a genuine HTML file."""
        db_path = _seed_db(tmp_path)
        # bob's balance differs (200 in DB, 999 in file) — seeded verdict.
        actual = _write_file(
            tmp_path / "actual.txt",
            [(1, "alice", "100"), (2, "bob", "999"), (3, "carol", "300")],
        )
        report = tmp_path / "report.html"

        result = compare_db_to_file(
            query_or_table="ACCOUNTS",
            mapping_config=_mapping(),
            actual_file=actual,
            output_format="html",
            key_columns=["ID"],
            connection_override=_override(db_path),
            output_path=str(report),
        )

        # A real file exists and is non-trivial (not the old no-op).
        assert report.exists(), "HTML report was not written (still a no-op)"
        html = report.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert "Valdo Comparison Report" in html
        # The verdict is rendered: the seeded mismatch value appears in the diff.
        assert "999" in html
        # Service surfaces the report path for downstream (CLI/API/MCP).
        assert result["report_path"] == str(report)

    def test_html_inferred_from_output_extension(self, tmp_path):
        """A ``.html`` output path renders HTML even when format defaults to json."""
        db_path = _seed_db(tmp_path)
        actual = _write_file(
            tmp_path / "actual2.txt",
            [(1, "alice", "100"), (2, "bob", "200"), (3, "carol", "300")],
        )
        report = tmp_path / "verdict.html"

        compare_db_to_file(
            query_or_table="ACCOUNTS",
            mapping_config=_mapping(),
            actual_file=actual,
            key_columns=["ID"],
            connection_override=_override(db_path),
            output_path=str(report),
        )

        assert report.exists()
        assert "<!DOCTYPE html>" in report.read_text(encoding="utf-8")

    def test_no_output_path_is_backward_compatible(self, tmp_path):
        """Without ``output_path`` the service renders nothing and adds no key."""
        db_path = _seed_db(tmp_path)
        actual = _write_file(
            tmp_path / "actual3.txt",
            [(1, "alice", "100"), (2, "bob", "200"), (3, "carol", "300")],
        )

        result = compare_db_to_file(
            query_or_table="ACCOUNTS",
            mapping_config=_mapping(),
            actual_file=actual,
            output_format="html",
            key_columns=["ID"],
            connection_override=_override(db_path),
        )

        assert "report_path" not in result


# ---------------------------------------------------------------------------
# CLI layer — consistent output contract (.html -> HTML, .json -> JSON)
# ---------------------------------------------------------------------------

class TestCliOutputContract:
    def _run(self, tmp_path, mapping_path, actual, db_path, output, output_format):
        return run_db_compare_command(
            query_or_table="ACCOUNTS",
            mapping=str(mapping_path),
            actual_file=actual,
            output_format=output_format,
            key_columns="ID",
            output=str(output),
            logger=MagicMock(),
            connection_override=_override(db_path),
        )

    def test_cli_html_output_writes_html(self, tmp_path):
        db_path = _seed_db(tmp_path)
        mapping_path = tmp_path / "mapping.json"
        mapping_path.write_text(json.dumps(_mapping()), encoding="utf-8")
        actual = _write_file(
            tmp_path / "actual.txt",
            [(1, "alice", "100"), (2, "bob", "999"), (3, "carol", "300")],
        )
        report = tmp_path / "cli_report.html"

        self._run(tmp_path, mapping_path, actual, db_path, report, "html")

        assert report.exists()
        html = report.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert "999" in html

    def test_cli_json_output_still_writes_json(self, tmp_path):
        """Regression guard: ``.json`` output stays machine JSON."""
        db_path = _seed_db(tmp_path)
        mapping_path = tmp_path / "mapping.json"
        mapping_path.write_text(json.dumps(_mapping()), encoding="utf-8")
        actual = _write_file(
            tmp_path / "actual.txt",
            [(1, "alice", "100"), (2, "bob", "200"), (3, "carol", "300")],
        )
        report = tmp_path / "cli_report.json"

        self._run(tmp_path, mapping_path, actual, db_path, report, "json")

        assert report.exists()
        # Parses as JSON and carries the unified result shape.
        parsed = json.loads(report.read_text(encoding="utf-8"))
        assert "workflow" in parsed
        assert "compare" in parsed


# ---------------------------------------------------------------------------
# REST layer — POST /db-compare with output_format=html surfaces report_url
# ---------------------------------------------------------------------------

class TestApiHtmlReportUrl:
    def test_html_output_format_returns_report_url(self, tmp_path, monkeypatch):
        """``output_format=html`` renders an HTML report and returns its URL."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        monkeypatch.setenv("API_KEYS", "test-key:admin")

        from src.api.main import app

        mapping_cfg = {
            "name": "accounts",
            "fields": [{"name": "ID"}, {"name": "NAME"}, {"name": "BALANCE"}],
        }
        (tmp_path / "acct_map.json").write_text(json.dumps(mapping_cfg))
        uploads = tmp_path / "uploads"
        uploads.mkdir()

        db_path = _seed_db(tmp_path)

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch("src.api.routers.files.UPLOADS_DIR", uploads),
            patch(
                "src.services.db_file_compare_service._build_adapter",
            ) as build_adapter,
        ):
            # Route the service's adapter to our real SQLite DB.
            from src.database.adapters.sqlite_adapter import SQLiteAdapter

            build_adapter.return_value = SQLiteAdapter(db_path=db_path)

            client = TestClient(app)
            resp = client.post(
                "/api/v1/files/db-compare",
                headers={"X-API-Key": "test-key"},
                data={
                    "query_or_table": "ACCOUNTS",
                    "mapping_id": "acct_map",
                    "key_columns": "ID",
                    "output_format": "html",
                },
                files={
                    "actual_file": (
                        "actual.txt",
                        b"ID|NAME|BALANCE\n1|alice|100\n2|bob|999\n3|carol|300\n",
                        "text/plain",
                    )
                },
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["report_url"] is not None
        assert body["report_url"].startswith("/uploads/")
        assert body["report_url"].endswith(".html")
        # The report was really written to UPLOADS_DIR.
        report_name = Path(body["report_url"]).name
        written = uploads / report_name
        assert written.exists()
        assert "<!DOCTYPE html>" in written.read_text(encoding="utf-8")
