"""Unit tests for the onboarding drift-detection helpers (S17-4, #432).

Covers the equivalence contract (compare_artefact_payload), the normalisation
primitives (metadata / SQL / path basename), parse/load helpers, and the
LRU+TTL ArtefactContentCache used by the EE-S3 preview path.
"""

import json

import pytest
import yaml

from src.onboarding import drift
from src.onboarding.drift import (
    ArtefactContentCache,
    ArtefactStatus,
    compare_artefact_payload,
    compute_workbook_hash,
    extract_committed_timestamp,
    get_artefact_content_cache,
    load_committed_artefact,
    normalise_metadata_for_compare,
    normalise_sql_for_compare,
    parse_emitted_artefact,
    path_basename,
    strip_metadata,
    summarise_dict_drift,
)


class TestStripMetadata:
    def test_drops_metadata_key(self):
        assert strip_metadata({"a": 1, "metadata": {"x": 2}}) == {"a": 1}

    def test_passes_through_non_dict(self):
        assert strip_metadata([1, 2]) == [1, 2]


class TestPathBasename:
    def test_windows_path(self):
        assert path_basename("C:\\dir\\sub\\file.csv") == "file.csv"

    def test_posix_path(self):
        assert path_basename("/dir/sub/file.csv") == "file.csv"

    def test_non_string_passthrough(self):
        assert path_basename(42) == 42


class TestExtractCommittedTimestamp:
    def test_returns_created_date(self):
        data = {"metadata": {"created_date": "2020-01-01"}}
        assert extract_committed_timestamp(data) == "2020-01-01"

    def test_missing_metadata_returns_none(self):
        assert extract_committed_timestamp({"x": 1}) is None
        assert extract_committed_timestamp("not-a-dict") is None
        assert extract_committed_timestamp({"metadata": "bad"}) is None
        assert extract_committed_timestamp({"metadata": {"created_date": 5}}) is None


class TestNormaliseMetadata:
    def test_non_dict_passthrough(self):
        e, c = normalise_metadata_for_compare("a", "b")
        assert (e, c) == ("a", "b")

    def test_timestamps_taken_from_committed(self):
        emitted = {"metadata": {"created_date": "EMIT", "k": 1}}
        committed = {"metadata": {"created_date": "COMMIT", "k": 1}}
        e, c = normalise_metadata_for_compare(emitted, committed)
        assert e["metadata"]["created_date"] == "COMMIT"
        assert c["metadata"]["created_date"] == "COMMIT"

    def test_emitted_only_timestamp_is_dropped(self):
        emitted = {"metadata": {"last_modified": "EMIT", "k": 1}}
        committed = {"metadata": {"k": 1}}
        e, c = normalise_metadata_for_compare(emitted, committed)
        assert "last_modified" not in e["metadata"]

    def test_template_paths_reduced_to_basename(self):
        emitted = {"metadata": {"source_template": "/posix/t.xlsx"}}
        committed = {"metadata": {"source_template": "C:\\win\\t.xlsx"}}
        e, c = normalise_metadata_for_compare(emitted, committed)
        assert e["metadata"]["source_template"] == "t.xlsx"
        assert c["metadata"]["source_template"] == "t.xlsx"

    def test_does_not_mutate_inputs(self):
        emitted = {"metadata": {"created_date": "EMIT"}}
        committed = {"metadata": {"created_date": "COMMIT"}}
        normalise_metadata_for_compare(emitted, committed)
        assert emitted["metadata"]["created_date"] == "EMIT"


class TestNormaliseSql:
    def test_strips_comments_and_collapses_whitespace(self):
        a = "SELECT a,  b\n  FROM t  -- a comment\nWHERE x = 1"
        b = "SELECT a, b FROM t WHERE x = 1"
        assert normalise_sql_for_compare(a) == normalise_sql_for_compare(b)

    def test_structurally_different_sql_differs(self):
        assert normalise_sql_for_compare("SELECT a FROM t") != normalise_sql_for_compare(
            "SELECT b FROM t"
        )


class TestParseEmitted:
    def test_source_yaml_parsed_as_yaml(self):
        from pathlib import Path

        out = parse_emitted_artefact("a: 1", "source_yaml", Path("x.json"))
        assert out == {"a": 1}

    def test_yaml_suffix_parsed_as_yaml(self):
        from pathlib import Path

        out = parse_emitted_artefact("a: 1", "mapping", Path("x.yml"))
        assert out == {"a": 1}

    def test_json_default(self):
        from pathlib import Path

        out = parse_emitted_artefact('{"a": 1}', "mapping", Path("x.json"))
        assert out == {"a": 1}


class TestLoadCommitted:
    def test_missing_file_returns_none_false(self, tmp_path):
        out, exists = load_committed_artefact(tmp_path / "nope.json", "mapping")
        assert out is None and exists is False

    def test_json_file(self, tmp_path):
        f = tmp_path / "m.json"
        f.write_text(json.dumps({"a": 1}))
        out, exists = load_committed_artefact(f, "mapping")
        assert out == {"a": 1} and exists is True

    def test_yaml_file(self, tmp_path):
        f = tmp_path / "m.yml"
        f.write_text("a: 1")
        out, exists = load_committed_artefact(f, "mapping")
        assert out == {"a": 1} and exists is True

    def test_source_yaml_category(self, tmp_path):
        f = tmp_path / "src.txt"
        f.write_text("a: 1")
        out, exists = load_committed_artefact(f, "source_yaml")
        assert out == {"a": 1} and exists is True


class TestSummariseDictDrift:
    def test_extra_emitted_key(self):
        msg = summarise_dict_drift({"a": 1, "b": 2}, {"a": 1})
        assert "extra key" in msg and "b" in msg

    def test_extra_committed_key(self):
        msg = summarise_dict_drift({"a": 1}, {"a": 1, "z": 2})
        assert "committed has extra" in msg and "z" in msg

    def test_value_differs(self):
        msg = summarise_dict_drift({"a": 1}, {"a": 2})
        assert "value for key 'a' differs" == msg

    def test_non_dicts_generic(self):
        assert summarise_dict_drift([1], [2]) == "structural values differ"


class TestCompareArtefactPayload:
    def test_sql_new_when_missing(self, tmp_path):
        status, msg = compare_artefact_payload(
            tmp_path / "q.sql", "SELECT 1", "sql"
        )
        assert status == ArtefactStatus.NEW and msg is None

    def test_sql_unchanged_whitespace_tolerant(self, tmp_path):
        f = tmp_path / "q.sql"
        f.write_text("SELECT a\n FROM t")
        status, msg = compare_artefact_payload(f, "SELECT a FROM t", "sql")
        assert status == ArtefactStatus.UNCHANGED

    def test_sql_changed(self, tmp_path):
        f = tmp_path / "q.sql"
        f.write_text("SELECT a FROM t")
        status, msg = compare_artefact_payload(f, "SELECT b FROM t", "sql")
        assert status == ArtefactStatus.CHANGED and "SQL structural drift" in msg

    def test_mapping_new(self, tmp_path):
        status, msg = compare_artefact_payload(
            tmp_path / "m.json", json.dumps({"a": 1}), "mapping"
        )
        assert status == ArtefactStatus.NEW

    def test_mapping_unchanged_with_metadata_normalisation(self, tmp_path):
        f = tmp_path / "m.json"
        f.write_text(json.dumps({"k": 1, "metadata": {"created_date": "OLD"}}))
        emitted = json.dumps({"k": 1, "metadata": {"created_date": "NEW"}})
        status, msg = compare_artefact_payload(f, emitted, "mapping")
        assert status == ArtefactStatus.UNCHANGED

    def test_mapping_changed(self, tmp_path):
        f = tmp_path / "m.json"
        f.write_text(json.dumps({"k": 1}))
        status, msg = compare_artefact_payload(f, json.dumps({"k": 2}), "mapping")
        assert status == ArtefactStatus.CHANGED and msg

    def test_source_yaml_unchanged(self, tmp_path):
        f = tmp_path / "s.yml"
        f.write_text("a: 1")
        status, msg = compare_artefact_payload(f, "a: 1", "source_yaml")
        assert status == ArtefactStatus.UNCHANGED

    def test_reconciliation_changed(self, tmp_path):
        f = tmp_path / "r.yml"
        f.write_text("a: 1")
        status, msg = compare_artefact_payload(f, "a: 2", "reconciliation")
        assert status == ArtefactStatus.CHANGED

    def test_emitted_parse_error_surfaced(self, tmp_path):
        f = tmp_path / "m.json"
        f.write_text(json.dumps({"k": 1}))
        status, msg = compare_artefact_payload(f, "{not valid json", "mapping")
        assert status == ArtefactStatus.CHANGED and "parse error" in msg

    def test_committed_parse_error_surfaced(self, tmp_path):
        f = tmp_path / "m.json"
        f.write_text("{broken json")
        status, msg = compare_artefact_payload(f, json.dumps({"k": 1}), "mapping")
        assert status == ArtefactStatus.CHANGED and "committed-side parse error" in msg


class TestWorkbookHash:
    def test_stable_hex_digest(self):
        h = compute_workbook_hash(b"abc")
        assert len(h) == 64
        assert h == compute_workbook_hash(b"abc")
        assert h != compute_workbook_hash(b"abd")


class TestArtefactContentCache:
    def test_rejects_bad_bounds(self):
        with pytest.raises(ValueError):
            ArtefactContentCache(max_entries=0)
        with pytest.raises(ValueError):
            ArtefactContentCache(ttl_seconds=0)

    def test_put_get_roundtrip_returns_copy(self):
        c = ArtefactContentCache()
        c.put("h1", {"p": "content"})
        got = c.get("h1")
        assert got == {"p": "content"}
        # mutating the returned dict must not affect the live entry
        got["p"] = "changed"
        assert c.get("h1") == {"p": "content"}

    def test_get_missing_returns_none(self):
        c = ArtefactContentCache()
        assert c.get("absent") is None

    def test_get_content_helper(self):
        c = ArtefactContentCache()
        c.put("h", {"a/b.json": "X"})
        assert c.get_content("h", "a/b.json") == "X"
        assert c.get_content("h", "missing") is None
        assert c.get_content("absent", "a/b.json") is None

    def test_lru_eviction_on_overflow(self):
        c = ArtefactContentCache(max_entries=2)
        c.put("a", {"x": "1"})
        c.put("b", {"x": "2"})
        c.put("c", {"x": "3"})  # evicts LRU "a"
        assert c.get("a") is None
        assert c.get("b") is not None
        assert c.get("c") is not None

    def test_lru_read_bumps_recency(self):
        c = ArtefactContentCache(max_entries=2)
        c.put("a", {"x": "1"})
        c.put("b", {"x": "2"})
        c.get("a")  # bump "a" to MRU
        c.put("c", {"x": "3"})  # should evict "b", not "a"
        assert c.get("a") is not None
        assert c.get("b") is None

    def test_ttl_expiry(self):
        clock = {"t": 0.0}
        c = ArtefactContentCache(ttl_seconds=10, time_fn=lambda: clock["t"])
        c.put("h", {"x": "1"})
        clock["t"] = 5
        assert c.get("h") is not None
        clock["t"] = 20  # past TTL
        assert c.get("h") is None

    def test_len_and_clear(self):
        c = ArtefactContentCache()
        c.put("a", {"x": "1"})
        c.put("b", {"x": "2"})
        assert len(c) == 2
        c.clear()
        assert len(c) == 0

    def test_singleton_accessor(self):
        drift._ARTEFACT_CONTENT_CACHE = None
        c1 = get_artefact_content_cache()
        c2 = get_artefact_content_cache()
        assert c1 is c2
        c1.clear()
