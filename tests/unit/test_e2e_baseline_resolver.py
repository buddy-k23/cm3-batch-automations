"""Unit tests for ``scripts.e2e_lib.baseline_resolver``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.baseline_resolver import (  # noqa: E402
    BaselineEntry,
    BaselineResolver,
    BaselineResolverError,
    read_mapping_version,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _manifest_with(*entries):
    return {"schema_version": 1, "baselines": list(entries)}


def _entry(**overrides):
    base = {
        "env": "sit",
        "source": "SRC_A",
        "file_type": "P327",
        "release_tag": "R2026.05",
        "mapping_version": "1.0.0",
        "baseline_file": "baselines/sit/SRC_A/R2026.05/P327.txt",
        "mapping_path": "config/mappings/SRC_A_P327.json",
        "approved_by": "qa@example.com",
        "approved_at": "2026-04-30T17:00:00Z",
        "comment": "initial",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def manifest_file(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            _manifest_with(
                _entry(),
                _entry(env="ait"),
                _entry(file_type="P328", mapping_version="1.0.0"),
                _entry(
                    file_type="P327",
                    mapping_version="1.1.0",
                    release_tag="R2026.06",
                    baseline_file="baselines/sit/SRC_A/R2026.06/P327.txt",
                    comment="mapping bumped",
                ),
            ),
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


class TestFromFile:
    def test_loads_valid_manifest(self, manifest_file: Path) -> None:
        r = BaselineResolver.from_file(manifest_file)
        assert len(r.entries) == 4
        assert r.manifest_path == manifest_file

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(BaselineResolverError, match="not found"):
            BaselineResolver.from_file(tmp_path / "no.json")

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(BaselineResolverError, match="failed to parse"):
            BaselineResolver.from_file(path)

    def test_wrong_top_level_type_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[1,2,3]", encoding="utf-8")
        with pytest.raises(BaselineResolverError, match="JSON object"):
            BaselineResolver.from_file(path)

    def test_unknown_schema_version_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "v2.json"
        path.write_text(
            json.dumps({"schema_version": 2, "baselines": []}),
            encoding="utf-8",
        )
        with pytest.raises(BaselineResolverError, match="schema_version"):
            BaselineResolver.from_file(path)

    def test_missing_schema_version_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "no_schema.json"
        path.write_text(json.dumps({"baselines": []}), encoding="utf-8")
        with pytest.raises(BaselineResolverError, match="schema_version"):
            BaselineResolver.from_file(path)

    def test_baselines_must_be_list(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text(
            json.dumps({"schema_version": 1, "baselines": {}}),
            encoding="utf-8",
        )
        with pytest.raises(BaselineResolverError, match="must be a list"):
            BaselineResolver.from_file(path)

    def test_entry_missing_required_keys_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text(
            json.dumps({"schema_version": 1, "baselines": [{"env": "sit"}]}),
            encoding="utf-8",
        )
        with pytest.raises(BaselineResolverError, match="missing required"):
            BaselineResolver.from_file(path)


# --------------------------------------------------------------------------- #
# Entry round-trip
# --------------------------------------------------------------------------- #


class TestBaselineEntry:
    def test_from_dict_to_dict_preserves_known_keys(self) -> None:
        e = BaselineEntry.from_dict(_entry())
        assert e.env == "sit"
        assert e.file_type == "P327"
        round_tripped = e.to_dict()
        for key in (
            "env",
            "source",
            "file_type",
            "release_tag",
            "mapping_version",
            "baseline_file",
            "mapping_path",
            "approved_by",
            "approved_at",
            "comment",
        ):
            assert round_tripped[key] == _entry()[key]

    def test_extras_are_preserved(self) -> None:
        data = _entry(checksum="abc123", reviewer="bob@example.com")
        e = BaselineEntry.from_dict(data)
        round_tripped = e.to_dict()
        assert round_tripped["checksum"] == "abc123"
        assert round_tripped["reviewer"] == "bob@example.com"


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #


class TestFind:
    def test_find_unique_match(self, manifest_file: Path) -> None:
        r = BaselineResolver.from_file(manifest_file)
        e = r.find(
            env="sit",
            source="SRC_A",
            file_type="P327",
            mapping_version="1.0.0",
        )
        assert e.release_tag == "R2026.05"
        assert e.baseline_file.endswith("R2026.05/P327.txt")

    def test_find_picks_newer_mapping_version(self, manifest_file: Path) -> None:
        r = BaselineResolver.from_file(manifest_file)
        e = r.find(
            env="sit",
            source="SRC_A",
            file_type="P327",
            mapping_version="1.1.0",
        )
        assert e.release_tag == "R2026.06"

    def test_no_match_raises_with_actionable_message(self, manifest_file: Path) -> None:
        r = BaselineResolver.from_file(manifest_file)
        with pytest.raises(BaselineResolverError) as exc:
            r.find(
                env="sit",
                source="SRC_A",
                file_type="P327",
                mapping_version="9.9.9",
            )
        # The message must mention promote_baseline so the operator knows
        # what to do next.
        assert "promote_baseline" in str(exc.value)
        assert "9.9.9" in str(exc.value)

    def test_duplicate_pin_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "dupes.json"
        path.write_text(
            json.dumps(_manifest_with(_entry(), _entry())),
            encoding="utf-8",
        )
        r = BaselineResolver.from_file(path)
        with pytest.raises(BaselineResolverError, match="corrupt"):
            r.find(
                env="sit",
                source="SRC_A",
                file_type="P327",
                mapping_version="1.0.0",
            )


# --------------------------------------------------------------------------- #
# Filtered listing
# --------------------------------------------------------------------------- #


class TestListFor:
    def test_filter_by_env(self, manifest_file: Path) -> None:
        r = BaselineResolver.from_file(manifest_file)
        assert len(r.list_for(env="ait")) == 1
        assert len(r.list_for(env="sit")) == 3

    def test_filter_by_source_and_file_type(self, manifest_file: Path) -> None:
        r = BaselineResolver.from_file(manifest_file)
        out = r.list_for(env="sit", source="SRC_A", file_type="P327")
        assert {e.mapping_version for e in out} == {"1.0.0", "1.1.0"}

    def test_unfiltered_returns_all(self, manifest_file: Path) -> None:
        r = BaselineResolver.from_file(manifest_file)
        assert len(r.list_for()) == len(r.entries)


# --------------------------------------------------------------------------- #
# CLI smoke
# --------------------------------------------------------------------------- #


class TestCli:
    def test_cli_resolves_known_entry(
        self, manifest_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from scripts.e2e_lib.baseline_resolver import _main

        rc = _main(
            [
                "--manifest",
                str(manifest_file),
                "--env",
                "sit",
                "--source",
                "SRC_A",
                "--file-type",
                "P327",
                "--mapping-version",
                "1.0.0",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["release_tag"] == "R2026.05"

    def test_cli_returns_nonzero_on_miss(
        self, manifest_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from scripts.e2e_lib.baseline_resolver import _main

        rc = _main(
            [
                "--manifest",
                str(manifest_file),
                "--env",
                "sit",
                "--source",
                "SRC_A",
                "--file-type",
                "P327",
                "--mapping-version",
                "9.9.9",
            ]
        )
        assert rc == 2
        assert "no baseline pinned" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Real on-disk manifest (the committed example)
# --------------------------------------------------------------------------- #


class TestCommittedManifest:
    def test_repo_manifest_loads(self) -> None:
        """The committed baselines/manifest.json must pass validation."""
        path = _REPO_ROOT / "baselines" / "manifest.json"
        r = BaselineResolver.from_file(path)
        assert any(
            e.source == "SRC_A" and e.env == "sit" and e.file_type == "P327"
            for e in r.entries
        )


# --------------------------------------------------------------------------- #
# Mapping-version reader (R-10b)
# --------------------------------------------------------------------------- #


def _write_mapping(path: Path, **fields) -> Path:
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


class TestReadMappingVersion:
    def test_reads_version_key(self, tmp_path: Path) -> None:
        p = _write_mapping(tmp_path / "m.json", version="2.0.0", fields=[])
        assert read_mapping_version(p) == "2.0.0"

    def test_reads_mapping_version_alias(self, tmp_path: Path) -> None:
        p = _write_mapping(tmp_path / "m.json", mapping_version="3.1.0")
        assert read_mapping_version(p) == "3.1.0"

    def test_version_takes_precedence_over_alias(self, tmp_path: Path) -> None:
        p = _write_mapping(
            tmp_path / "m.json", version="2.0.0", mapping_version="9.9.9"
        )
        assert read_mapping_version(p) == "2.0.0"

    def test_missing_version_is_unversioned(self, tmp_path: Path) -> None:
        p = _write_mapping(tmp_path / "m.json", fields=[])
        assert read_mapping_version(p) == "unversioned"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(BaselineResolverError, match="mapping not found"):
            read_mapping_version(tmp_path / "nope.json")

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(BaselineResolverError, match="failed to parse"):
            read_mapping_version(p)

    def test_non_object_top_level_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "list.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(BaselineResolverError, match="JSON object"):
            read_mapping_version(p)


# --------------------------------------------------------------------------- #
# resolve_for_mapping — the mapping↔baseline pinning contract (R-10b)
# --------------------------------------------------------------------------- #


class TestResolveForMapping:
    def test_matching_version_resolves(
        self, manifest_file: Path, tmp_path: Path
    ) -> None:
        """A mapping pinned to a present baseline version resolves cleanly."""
        mapping = _write_mapping(
            tmp_path / "SRC_A_P327.json", version="1.0.0", fields=[]
        )
        r = BaselineResolver.from_file(manifest_file)
        entry = r.resolve_for_mapping(
            env="sit", source="SRC_A", file_type="P327", mapping_path=mapping
        )
        assert entry.mapping_version == "1.0.0"
        assert entry.release_tag == "R2026.05"

    def test_matching_newer_version_resolves(
        self, manifest_file: Path, tmp_path: Path
    ) -> None:
        """Bumping the mapping to a version that HAS a baseline resolves it."""
        mapping = _write_mapping(
            tmp_path / "SRC_A_P327.json", version="1.1.0", fields=[]
        )
        r = BaselineResolver.from_file(manifest_file)
        entry = r.resolve_for_mapping(
            env="sit", source="SRC_A", file_type="P327", mapping_path=mapping
        )
        assert entry.mapping_version == "1.1.0"
        assert entry.release_tag == "R2026.06"

    def test_mismatching_version_fails_fast(
        self, manifest_file: Path, tmp_path: Path
    ) -> None:
        """A mapping bumped past every pinned baseline fails fast.

        This is the core R-10b guarantee: an MR that bumps mapping_version
        without refreshing the baseline must NOT silently compare against a
        stale baseline; it must error with an actionable message.
        """
        mapping = _write_mapping(
            tmp_path / "SRC_A_P327.json", version="2.0.0", fields=[]
        )
        r = BaselineResolver.from_file(manifest_file)
        with pytest.raises(BaselineResolverError) as exc:
            r.resolve_for_mapping(
                env="sit",
                source="SRC_A",
                file_type="P327",
                mapping_path=mapping,
            )
        msg = str(exc.value)
        assert "2.0.0" in msg
        assert "promote_baseline" in msg

    def test_mismatch_detected_even_if_find_relaxes_key(
        self, manifest_file: Path, tmp_path: Path, monkeypatch
    ) -> None:
        """The explicit cross-check catches a wrong-version entry.

        Guards against a future ``find`` that no longer keys strictly on
        ``mapping_version``: ``resolve_for_mapping`` must still reject an
        entry whose pinned version differs from the live mapping.
        """
        mapping = _write_mapping(
            tmp_path / "SRC_A_P327.json", version="2.0.0", fields=[]
        )
        r = BaselineResolver.from_file(manifest_file)

        # Simulate a relaxed find() that returns a stale 1.0.0-pinned entry
        # despite the caller asking for 2.0.0.
        stale = r.find(
            env="sit",
            source="SRC_A",
            file_type="P327",
            mapping_version="1.0.0",
        )
        monkeypatch.setattr(r, "find", lambda **_: stale)

        with pytest.raises(BaselineResolverError, match="version mismatch"):
            r.resolve_for_mapping(
                env="sit",
                source="SRC_A",
                file_type="P327",
                mapping_path=mapping,
            )

    def test_unversioned_mapping_resolves_against_unversioned_pin(
        self, tmp_path: Path
    ) -> None:
        """A mapping with no version key resolves against an 'unversioned' pin."""
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps(_manifest_with(_entry(mapping_version="unversioned"))),
            encoding="utf-8",
        )
        mapping = _write_mapping(tmp_path / "m.json", fields=[])
        r = BaselineResolver.from_file(manifest)
        entry = r.resolve_for_mapping(
            env="sit", source="SRC_A", file_type="P327", mapping_path=mapping
        )
        assert entry.mapping_version == "unversioned"

    def test_missing_mapping_file_raises(
        self, manifest_file: Path, tmp_path: Path
    ) -> None:
        r = BaselineResolver.from_file(manifest_file)
        with pytest.raises(BaselineResolverError, match="mapping not found"):
            r.resolve_for_mapping(
                env="sit",
                source="SRC_A",
                file_type="P327",
                mapping_path=tmp_path / "missing.json",
            )
