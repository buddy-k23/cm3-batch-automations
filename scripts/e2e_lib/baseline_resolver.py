"""Look up the right baseline file for a given (env, source, file_type, mapping_version).

This module is the M5 entry point for the **baseline-version manifest +
promote** capability described in ``prompts/e2e_batch_testing_prompt.md``.

Design
------
* **Single JSON manifest** at ``baselines/manifest.json``. Each entry pins one
  ``(env, source, file_type)`` tuple to a ``release_tag`` plus
  ``mapping_version`` pair, plus the path of the baseline file on disk.
* **Append-only history**. Multiple entries per tuple form a chronological
  trail. :meth:`BaselineResolver.find` returns the single entry whose
  ``mapping_version`` matches the caller's pinned version; an unmatched
  version fails fast (the L3 gate fails before any compare runs).
* **Pure**. The resolver does I/O exactly once (to load the manifest); after
  that every call is a dict lookup. No env-var reads, no DB, no Valdo
  internals.

CLI
---
Not a primary CLI; the resolver is intended to be imported. A trivial
``__main__`` entry point is provided for shell-level smoke tests::

    python -m scripts.e2e_lib.baseline_resolver \\
        --manifest baselines/manifest.json \\
        --env sit --source SRC_A --file-type P327 --mapping-version 1.0.0
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Required keys on every manifest entry. Extra keys are allowed (forward-
# compatibility: e.g. a future ``checksum`` field).
_REQUIRED_KEYS = (
    "env",
    "source",
    "file_type",
    "release_tag",
    "mapping_version",
    "baseline_file",
)


class BaselineResolverError(RuntimeError):
    """Raised for any baseline-resolution failure mode."""


# Sentinel used when a mapping JSON declares neither ``version`` nor
# ``mapping_version``. Mirrors ``promote_baseline.resolve_mapping_version`` so
# that an unversioned mapping pins (and resolves) against an "unversioned"
# baseline rather than failing in a way that masks the real contract.
_UNVERSIONED = "unversioned"


def read_mapping_version(mapping_path: Path) -> str:
    """Read the version recorded in a mapping JSON.

    The mapping JSON may carry the version under either ``version`` or
    ``mapping_version`` (the manifest pins the latter name). This is the
    single read-side counterpart to
    :func:`scripts.e2e_lib.promote_baseline.resolve_mapping_version`, kept
    here (dependency-free) so the resolver can enforce the
    mapping↔baseline version-pinning contract without importing the
    promote tool.

    Args:
        mapping_path: Path to the mapping JSON on disk.

    Returns:
        The declared version as a string, or ``"unversioned"`` when the
        mapping declares neither key.

    Raises:
        BaselineResolverError: If the file is missing, unreadable, not
            valid JSON, or not a JSON object.
    """
    mapping_path = Path(mapping_path)
    if not mapping_path.is_file():
        raise BaselineResolverError(f"mapping not found: {mapping_path}")
    try:
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BaselineResolverError(
            f"failed to parse mapping {mapping_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise BaselineResolverError(
            f"mapping {mapping_path} top level must be a JSON object"
        )
    version = data.get("version") or data.get("mapping_version")
    if not version:
        return _UNVERSIONED
    return str(version)


@dataclass(frozen=True)
class BaselineEntry:
    """One pinned baseline.

    Attributes mirror the on-disk manifest schema. ``approved_by``,
    ``approved_at``, ``comment``, and ``mapping_path`` are advisory and may
    be empty strings if the operator did not supply them.
    """

    env: str
    source: str
    file_type: str
    release_tag: str
    mapping_version: str
    baseline_file: str
    mapping_path: str = ""
    approved_by: str = ""
    approved_at: str = ""
    comment: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaselineEntry":
        missing = [k for k in _REQUIRED_KEYS if k not in data]
        if missing:
            raise BaselineResolverError(
                f"manifest entry missing required keys: {missing}; entry={data!r}"
            )
        known = set(_REQUIRED_KEYS) | {
            "mapping_path",
            "approved_by",
            "approved_at",
            "comment",
        }
        extras = {k: v for k, v in data.items() if k not in known}
        return cls(
            env=str(data["env"]),
            source=str(data["source"]),
            file_type=str(data["file_type"]),
            release_tag=str(data["release_tag"]),
            mapping_version=str(data["mapping_version"]),
            baseline_file=str(data["baseline_file"]),
            mapping_path=str(data.get("mapping_path", "")),
            approved_by=str(data.get("approved_by", "")),
            approved_at=str(data.get("approved_at", "")),
            comment=str(data.get("comment", "")),
            extras=extras,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Round-trip back to the manifest shape (preserves ``extras``)."""
        out: Dict[str, Any] = {
            "env": self.env,
            "source": self.source,
            "file_type": self.file_type,
            "release_tag": self.release_tag,
            "mapping_version": self.mapping_version,
            "baseline_file": self.baseline_file,
            "mapping_path": self.mapping_path,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "comment": self.comment,
        }
        out.update(self.extras)
        return out


@dataclass
class BaselineResolver:
    """Loads the manifest once and serves lookups against the parsed entries."""

    entries: List[BaselineEntry]
    manifest_path: Optional[Path] = None

    # ----- factories ------------------------------------------------- #

    @classmethod
    def from_file(cls, manifest_path: Path) -> "BaselineResolver":
        """Load and parse ``baselines/manifest.json``.

        Raises:
            BaselineResolverError: If the file is missing, malformed,
                or violates the schema.
        """
        manifest_path = Path(manifest_path)
        if not manifest_path.is_file():
            raise BaselineResolverError(f"manifest not found: {manifest_path}")
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BaselineResolverError(
                f"failed to parse {manifest_path}: {exc}"
            ) from exc
        return cls.from_dict(data, manifest_path=manifest_path)

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        *,
        manifest_path: Optional[Path] = None,
    ) -> "BaselineResolver":
        if not isinstance(data, dict):
            raise BaselineResolverError("manifest top-level must be a JSON object")
        # schema_version is required; we accept 1 only today.
        schema_version = data.get("schema_version")
        if schema_version != 1:
            raise BaselineResolverError(
                f"unsupported manifest schema_version={schema_version!r}; expected 1"
            )
        raw_entries = data.get("baselines")
        if not isinstance(raw_entries, list):
            raise BaselineResolverError("manifest 'baselines' must be a list")
        entries: List[BaselineEntry] = []
        for i, item in enumerate(raw_entries):
            if not isinstance(item, dict):
                raise BaselineResolverError(
                    f"manifest baselines[{i}] must be a JSON object"
                )
            entries.append(BaselineEntry.from_dict(item))
        return cls(entries=entries, manifest_path=manifest_path)

    # ----- queries --------------------------------------------------- #

    def list_for(
        self,
        *,
        env: Optional[str] = None,
        source: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> List[BaselineEntry]:
        """Return every entry matching the supplied filters (any combination)."""
        out: List[BaselineEntry] = []
        for e in self.entries:
            if env is not None and e.env != env:
                continue
            if source is not None and e.source != source:
                continue
            if file_type is not None and e.file_type != file_type:
                continue
            out.append(e)
        return out

    def find(
        self,
        *,
        env: str,
        source: str,
        file_type: str,
        mapping_version: str,
    ) -> BaselineEntry:
        """Resolve the unique entry for ``(env, source, file_type, mapping_version)``.

        Raises:
            BaselineResolverError: If zero or multiple entries match.
                Multiple matches indicate the manifest has a duplicate pin
                (an operator error in promote_baseline). Zero matches mean
                an MR bumped the mapping_version without refreshing the
                baseline — the L3 gate must fail fast.
        """
        matches = [
            e
            for e in self.entries
            if e.env == env
            and e.source == source
            and e.file_type == file_type
            and e.mapping_version == mapping_version
        ]
        if not matches:
            raise BaselineResolverError(
                f"no baseline pinned for "
                f"env={env!r} source={source!r} file_type={file_type!r} "
                f"mapping_version={mapping_version!r}. "
                "Refresh the baseline via scripts/promote_baseline.sh."
            )
        if len(matches) > 1:
            tags = [e.release_tag for e in matches]
            raise BaselineResolverError(
                f"manifest has {len(matches)} entries for "
                f"env={env!r} source={source!r} file_type={file_type!r} "
                f"mapping_version={mapping_version!r} "
                f"(release_tags={tags}). Manifest is corrupt; promote_baseline "
                "must produce exactly one pin per tuple."
            )
        return matches[0]

    def resolve_for_mapping(
        self,
        *,
        env: str,
        source: str,
        file_type: str,
        mapping_path: Path,
    ) -> BaselineEntry:
        """Resolve the baseline pinned to the *live* version of a mapping.

        This is the enforcement point for the mapping↔baseline version
        pinning contract (R-10b). Rather than trusting a caller-supplied
        ``mapping_version`` (as :meth:`find` does), it reads the version
        recorded in the mapping JSON on disk via :func:`read_mapping_version`
        and resolves against *that*. It then cross-checks the resolved
        entry: if its pinned ``mapping_version`` does not equal the live
        version, it fails fast with an actionable message instead of
        silently comparing against a stale baseline.

        The cross-check is belt-and-braces: :meth:`find` already keys on the
        version, so a mismatch normally surfaces as a no-match there. The
        extra assertion guards against a future :meth:`find` that relaxes
        the key, and makes the contract explicit at the call site.

        Args:
            env: Environment, e.g. ``"sit"``.
            source: Source system identifier.
            file_type: Output file type / record type.
            mapping_path: Path to the mapping JSON whose version is the
                source of truth for this comparison.

        Returns:
            The unique :class:`BaselineEntry` pinned to the live mapping
            version.

        Raises:
            BaselineResolverError: If the mapping cannot be read, no
                baseline is pinned to the live version, multiple entries
                match, or the resolved entry's pinned version disagrees
                with the live mapping version.
        """
        live_version = read_mapping_version(mapping_path)
        entry = self.find(
            env=env,
            source=source,
            file_type=file_type,
            mapping_version=live_version,
        )
        if entry.mapping_version != live_version:
            raise BaselineResolverError(
                f"mapping↔baseline version mismatch for "
                f"env={env!r} source={source!r} file_type={file_type!r}: "
                f"mapping {str(mapping_path)!r} declares "
                f"mapping_version={live_version!r} but the resolved baseline "
                f"({entry.baseline_file!r}, release_tag={entry.release_tag!r}) "
                f"is pinned to mapping_version={entry.mapping_version!r}. "
                "Refresh the baseline via scripts/promote_baseline.sh so the "
                "pin matches the mapping; never compare against a baseline "
                "built for a different mapping version."
            )
        return entry


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #


def _main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="baseline_resolver",
        description="Look up a baseline pin from baselines/manifest.json.",
    )
    p.add_argument("--manifest", default="baselines/manifest.json")
    p.add_argument("--env", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--file-type", required=True)
    p.add_argument("--mapping-version", required=True)
    args = p.parse_args(argv)

    try:
        resolver = BaselineResolver.from_file(Path(args.manifest))
        entry = resolver.find(
            env=args.env,
            source=args.source,
            file_type=args.file_type,
            mapping_version=args.mapping_version,
        )
    except BaselineResolverError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(entry.to_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
