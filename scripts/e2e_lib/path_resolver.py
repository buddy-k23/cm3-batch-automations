"""Source/environment-aware path resolver for the E2E batch testing harness.

Resolves templated paths declared in ``config/e2e/paths.yml`` and per-source
overlays in ``config/e2e/sources/<SOURCE>.yml``. Also classifies filenames
into ``(source, file_type, direction)`` using the regex patterns declared in
``paths.yml``.

Design notes
------------
- Pure functions; no side effects, no I/O beyond reading the two YAML files.
- No environment-variable lookups happen here. Secret resolution is the job of
  :mod:`scripts.e2e_lib.secret_resolver`. ``paths.yml`` only stores the *names*
  of the env vars that hold each environment's Oracle credentials.
- Placeholder grammar uses Python ``str.format``-style braces:
  ``{env}``, ``{source}``, ``{run_id}``, ``{file_type}``, ``{release_tag}``.
  Unknown placeholders raise :class:`PathResolverError` so typos fail fast.

Public API
----------
- :class:`PathResolver` — preferred entry point; load once, reuse across the
  harness.
- :class:`MatchedFile` — return type of :meth:`PathResolver.classify_filename`.
- :class:`PathResolverError` — single exception type for every failure mode.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

# Placeholders allowed inside any templated path string. Anything else is a
# typo and must fail fast.
_ALLOWED_PLACEHOLDERS = frozenset(
    {"env", "source", "run_id", "file_type", "release_tag"}
)

_DIRECTION_VALUES = frozenset({"input", "output"})

# Per-source overlays may override these env-level keys with literal absolute
# values. Any other key appearing at the top level of a source YAML that
# clashes with an env-level path key is treated as a configuration defect
# (fail fast). Extend this set when a new override is intentionally adopted.
#
# Source-level overrides are LITERAL: they must not contain ``{placeholder}``
# braces. They replace the env value wholesale for the given source.
_SOURCE_OVERRIDABLE_KEYS = frozenset({"output_root", "staging_schema"})

# Env-level keys a source overlay must never redefine. Kept distinct from
# ``_SOURCE_OVERRIDABLE_KEYS`` so the allow-list is explicit and additions
# require a code change (and an ADR).
_SOURCE_OVERRIDE_FORBIDDEN_KEYS = frozenset(
    {
        "input_root",
        "trigger_root",
        "report_root",
        "work_root",
        "baseline_root",
        "log_root",
        "oracle_dsn_env",
        "oracle_user_env",
        "oracle_password_env",
        "audit_schema",
    }
)


class PathResolverError(ValueError):
    """Raised for any path-resolver misuse or config defect."""


@dataclass(frozen=True)
class MatchedFile:
    """Result of classifying a filename against ``filename_patterns``.

    Attributes:
        source: The source system extracted from the filename (named group
            ``source``).
        file_type: The file-type tag extracted from the filename (named group
            ``file_type``).
        direction: ``"input"`` or ``"output"``.
        pattern_name: The ``name`` of the winning entry in ``filename_patterns``,
            useful for logging.
    """

    source: str
    file_type: str
    direction: str
    pattern_name: str


@dataclass
class _CompiledPattern:
    name: str
    regex: re.Pattern[str]
    direction: str


@dataclass
class PathResolver:
    """Loads and resolves the E2E path configuration.

    Attributes:
        paths_config: Parsed contents of ``config/e2e/paths.yml``.
        sources_dir: Directory containing per-source YAML overlays.
    """

    paths_config: Dict[str, Any]
    sources_dir: Path
    _patterns: List[_CompiledPattern] = field(default_factory=list, init=False)
    _source_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict, init=False)
    # Per-source compiled override patterns (R-07). Keyed by source name and
    # populated lazily; only present for sources that declare their own
    # ``filename_patterns`` overlay. ``None`` means "checked, no override".
    _source_patterns: Dict[str, Optional[List[_CompiledPattern]]] = field(
        default_factory=dict, init=False
    )

    # ----- construction -------------------------------------------------- #

    @classmethod
    def from_files(
        cls,
        paths_yaml: Path,
        sources_dir: Path,
    ) -> "PathResolver":
        """Build a :class:`PathResolver` from on-disk YAML files.

        Args:
            paths_yaml: Path to ``config/e2e/paths.yml``.
            sources_dir: Path to the directory holding per-source YAML files.

        Returns:
            A fully validated :class:`PathResolver` instance.

        Raises:
            PathResolverError: If either path is missing or malformed.
        """
        paths_yaml = Path(paths_yaml)
        sources_dir = Path(sources_dir)
        if not paths_yaml.is_file():
            raise PathResolverError(f"paths.yml not found: {paths_yaml}")
        if not sources_dir.is_dir():
            raise PathResolverError(f"sources directory not found: {sources_dir}")
        try:
            data = yaml.safe_load(paths_yaml.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise PathResolverError(f"failed to parse {paths_yaml}: {exc}") from exc
        if not isinstance(data, dict):
            raise PathResolverError(
                f"{paths_yaml} must contain a YAML mapping at the top level"
            )
        resolver = cls(paths_config=data, sources_dir=sources_dir)
        resolver._validate_and_compile()
        return resolver

    # ----- env lookup ---------------------------------------------------- #

    def env_config(self, env: str) -> Dict[str, Any]:
        """Return the raw config block for ``env`` (e.g. ``sit`` or ``ait``).

        Raises:
            PathResolverError: If ``env`` is not declared in ``paths.yml``.
        """
        envs = self.paths_config.get("envs", {})
        if env not in envs:
            known = ", ".join(sorted(envs)) or "<none>"
            raise PathResolverError(f"unknown env '{env}'. Known envs: {known}")
        return envs[env]

    def known_envs(self) -> List[str]:
        """Return the sorted list of declared environments."""
        return sorted(self.paths_config.get("envs", {}))

    # ----- path resolution ---------------------------------------------- #

    def resolve(
        self,
        key: str,
        *,
        env: str,
        source: Optional[str] = None,
        run_id: Optional[str] = None,
        file_type: Optional[str] = None,
        release_tag: Optional[str] = None,
    ) -> str:
        """Resolve a templated path under ``envs.<env>.<key>``.

        Args:
            key: The name of the path entry inside the env block, e.g.
                ``"input_root"`` or ``"report_root"``.
            env: The environment name.
            source: Optional source system, substituted for ``{source}``.
            run_id: Optional run identifier, substituted for ``{run_id}``.
            file_type: Optional file-type tag, substituted for ``{file_type}``.
            release_tag: Optional release tag, substituted for ``{release_tag}``.
                When omitted, the per-source ``release_tag`` is used if a
                ``source`` is supplied.

        Returns:
            The resolved path as a string with all placeholders substituted.

        Raises:
            PathResolverError: If the key is missing, an unsupported
                placeholder is referenced, or a required placeholder was not
                supplied.
        """
        env_cfg = self.env_config(env)

        # Source-level override (F1 / F1b): when a source overlay defines
        # one of the allow-listed keys, it wins over the env-level value.
        # The override is a LITERAL — no placeholder substitution applied.
        if source is not None and key in _SOURCE_OVERRIDABLE_KEYS:
            override = self.source_config(source).get(key)
            if override is not None:
                if not isinstance(override, str):
                    raise PathResolverError(
                        f"source '{source}' override '{key}' must be a "
                        f"string, got {type(override).__name__}"
                    )
                if "{" in override or "}" in override:
                    raise PathResolverError(
                        f"source '{source}' override '{key}' must be a "
                        f"literal value (no '{{placeholder}}' braces): "
                        f"{override!r}"
                    )
                return override

        if key not in env_cfg:
            raise PathResolverError(f"env '{env}' has no path entry named '{key}'")
        template = env_cfg[key]
        if not isinstance(template, str):
            # Non-template scalars (e.g. schema names) are returned as-is.
            return str(template)

        # Fall back to per-source release_tag if needed.
        if release_tag is None and source is not None and "{release_tag}" in template:
            release_tag = self.source_config(source).get("release_tag")

        substitutions = {
            "env": env,
            "source": source,
            "run_id": run_id,
            "file_type": file_type,
            "release_tag": release_tag,
        }
        return _safe_format(template, substitutions)

    # ----- per-source config -------------------------------------------- #

    def source_config(self, source: str) -> Dict[str, Any]:
        """Return the parsed YAML overlay for ``source`` (cached)."""
        if source in self._source_cache:
            return self._source_cache[source]
        path = self.sources_dir / f"{source}.yml"
        if not path.is_file():
            raise PathResolverError(f"source config not found: {path}")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise PathResolverError(f"failed to parse {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise PathResolverError(
                f"{path} must contain a YAML mapping at the top level"
            )
        if data.get("source") != source:
            raise PathResolverError(
                f"source mismatch in {path}: file declares "
                f"source='{data.get('source')}', expected '{source}'"
            )
        # Fail fast on disallowed source-level overrides. The allow-list is
        # ``_SOURCE_OVERRIDABLE_KEYS``; any other env-level path key
        # appearing at the top of a source overlay is a configuration
        # defect (operator probably meant to override at the env level or
        # mistyped a key name).
        forbidden_present = _SOURCE_OVERRIDE_FORBIDDEN_KEYS & set(data)
        if forbidden_present:
            raise PathResolverError(
                f"source '{source}' overlay at {path} attempts to override "
                f"env-level key(s) {sorted(forbidden_present)} which is not "
                f"allowed. Allowed source-level overrides: "
                f"{sorted(_SOURCE_OVERRIDABLE_KEYS)}"
            )
        self._source_cache[source] = data
        return data

    # ----- filename classification -------------------------------------- #

    def classify_filename(
        self, filename: str, *, source: Optional[str] = None
    ) -> Optional[MatchedFile]:
        """Classify a basename against the configured filename patterns.

        Pattern selection (R-07): when ``source`` is given and that source's
        overlay declares its own ``filename_patterns`` block, those patterns
        **replace** the global set for this classification (no merging) so a
        non-CA-ESP producer can own its naming grammar without touching the
        shared ``paths.yml``. When ``source`` is omitted, or the source has no
        override, the global ``paths.yml`` patterns are used (the historical
        behaviour, unchanged for every existing source).

        Args:
            filename: A bare filename (no directory component).
            source: Optional source whose per-source ``filename_patterns``
                override should be used if present.

        Returns:
            A :class:`MatchedFile` if a pattern matched, otherwise ``None``.
            Patterns are evaluated in declaration order; first match wins.
        """
        patterns = self._patterns_for(source)
        for pat in patterns:
            m = pat.regex.match(filename)
            if not m:
                continue
            groups = m.groupdict()
            return MatchedFile(
                source=groups["source"],
                file_type=groups["file_type"],
                direction=pat.direction,
                pattern_name=pat.name,
            )
        return None

    def _patterns_for(self, source: Optional[str]) -> List[_CompiledPattern]:
        """Return the compiled patterns to use for ``source`` (R-07).

        Returns the source's override patterns when it declares a
        ``filename_patterns`` block, otherwise the global patterns. Override
        patterns are compiled once and cached per source.

        Args:
            source: Source name, or ``None`` for the global patterns.

        Returns:
            The ordered list of compiled patterns to evaluate.
        """
        if source is None:
            return self._patterns
        if source not in self._source_patterns:
            self._source_patterns[source] = self._compile_source_patterns(source)
        override = self._source_patterns[source]
        return override if override is not None else self._patterns

    def _compile_source_patterns(self, source: str) -> Optional[List[_CompiledPattern]]:
        """Compile a source overlay's ``filename_patterns`` block, if any.

        Args:
            source: Source name whose overlay is inspected.

        Returns:
            The compiled override patterns, or ``None`` when the source does
            not declare a ``filename_patterns`` block.

        Raises:
            PathResolverError: When the override block is malformed (same
                contract as the global block: each entry needs a unique name, a
                valid regex with ``source``/``file_type`` named groups, and a
                ``direction`` of ``input`` or ``output``).
        """
        cfg = self.source_config(source)
        raw_patterns = cfg.get("filename_patterns")
        if raw_patterns is None:
            return None
        if not isinstance(raw_patterns, list) or not raw_patterns:
            raise PathResolverError(
                f"source '{source}' 'filename_patterns' must be a non-empty "
                f"list when present"
            )
        return _compile_patterns(
            raw_patterns, context=f"source '{source}' filename_patterns"
        )

    # ----- trigger-file naming ------------------------------------------ #

    def trigger_config(self, source: Optional[str] = None) -> Dict[str, Any]:
        """Return the trigger-file naming contract for ``source`` (R-07).

        Resolves the ``.trigger`` sidecar ``suffix`` and the 1-indexed
        ``data_file_line`` (which line inside the sidecar carries the data
        filename). The global ``trigger_file`` block in ``paths.yml`` supplies
        the defaults; a source overlay may override either key in its own
        ``trigger_file`` block so a non-CA-ESP producer can use a different
        convention (e.g. ``.done``) without changing the shared default.

        Args:
            source: Optional source whose ``trigger_file`` override should be
                consulted. When ``None`` or the source has no override, the
                global values are returned.

        Returns:
            A dict with keys ``suffix`` (str, defaults to ``".trigger"``) and
            ``data_file_line`` (int, defaults to ``1``).

        Raises:
            PathResolverError: When an override value has the wrong type or an
                out-of-range ``data_file_line`` (< 1).
        """
        global_cfg = self.paths_config.get("trigger_file") or {}
        suffix = global_cfg.get("suffix") or ".trigger"
        data_file_line = global_cfg.get("data_file_line") or 1

        if source is not None:
            src_cfg = self.source_config(source).get("trigger_file") or {}
            if "suffix" in src_cfg:
                suffix = src_cfg["suffix"]
            if "data_file_line" in src_cfg:
                data_file_line = src_cfg["data_file_line"]

        if not isinstance(suffix, str) or not suffix:
            raise PathResolverError(
                f"trigger_file.suffix must be a non-empty string, got " f"{suffix!r}"
            )
        if (
            not isinstance(data_file_line, int)
            or isinstance(data_file_line, bool)
            or data_file_line < 1
        ):
            raise PathResolverError(
                f"trigger_file.data_file_line must be an integer >= 1, got "
                f"{data_file_line!r}"
            )
        return {"suffix": suffix, "data_file_line": data_file_line}

    # ----- internals ----------------------------------------------------- #

    def _validate_and_compile(self) -> None:
        """Validate ``paths.yml`` structure and pre-compile regex patterns."""
        envs = self.paths_config.get("envs")
        if not isinstance(envs, dict) or not envs:
            raise PathResolverError("paths.yml must declare a non-empty 'envs' mapping")

        required_env_keys = {
            "input_root",
            "output_root",
            "trigger_root",
            "report_root",
            "work_root",
            "baseline_root",
            "log_root",
            "oracle_dsn_env",
            "oracle_user_env",
            "oracle_password_env",
            "staging_schema",
            "audit_schema",
        }
        for env_name, env_cfg in envs.items():
            if not isinstance(env_cfg, dict):
                raise PathResolverError(f"env '{env_name}' must be a mapping")
            missing = required_env_keys - set(env_cfg)
            if missing:
                raise PathResolverError(
                    f"env '{env_name}' missing keys: " f"{sorted(missing)}"
                )
            for key, value in env_cfg.items():
                if isinstance(value, str):
                    _check_placeholders(value, key, env_name)

        patterns = self.paths_config.get("filename_patterns") or []
        if not isinstance(patterns, list):
            raise PathResolverError("'filename_patterns' must be a list")
        self._patterns = _compile_patterns(patterns, context="filename_patterns")


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _compile_patterns(patterns: List[Any], *, context: str) -> List[_CompiledPattern]:
    """Validate and compile a list of filename-pattern entries.

    Shared by the global ``paths.yml`` ``filename_patterns`` block and the
    per-source ``filename_patterns`` overrides (R-07), so both enforce the
    identical contract: each entry must be a mapping with a unique ``name``, a
    valid regex ``pattern`` carrying ``source`` and ``file_type`` named groups,
    and a ``direction`` of ``input`` or ``output``.

    Args:
        patterns: The raw list of pattern entries from YAML.
        context: Human-readable origin used in error messages (e.g.
            ``"filename_patterns"`` or ``"source 'SHAW' filename_patterns"``).

    Returns:
        The ordered list of compiled patterns.

    Raises:
        PathResolverError: On any malformed entry.
    """
    compiled: List[_CompiledPattern] = []
    seen_names: set[str] = set()
    for idx, entry in enumerate(patterns):
        if not isinstance(entry, dict):
            raise PathResolverError(f"{context}[{idx}] must be a mapping")
        name = entry.get("name") or f"pattern_{idx}"
        if name in seen_names:
            raise PathResolverError(f"{context}: duplicate pattern name: '{name}'")
        seen_names.add(name)
        raw = entry.get("pattern")
        if not isinstance(raw, str) or not raw:
            raise PathResolverError(f"{context}[{idx}] missing 'pattern'")
        direction = entry.get("direction")
        if direction not in _DIRECTION_VALUES:
            raise PathResolverError(
                f"{context}[{idx}] direction must be one of "
                f"{sorted(_DIRECTION_VALUES)}, got {direction!r}"
            )
        try:
            regex = re.compile(raw)
        except re.error as exc:
            raise PathResolverError(f"{context}[{idx}] invalid regex: {exc}") from exc
        required_groups = {"source", "file_type"}
        missing_groups = required_groups - set(regex.groupindex)
        if missing_groups:
            raise PathResolverError(
                f"{context}[{idx}] missing named groups: " f"{sorted(missing_groups)}"
            )
        compiled.append(_CompiledPattern(name=name, regex=regex, direction=direction))
    return compiled


def _check_placeholders(template: str, key: str, env: str) -> None:
    """Reject unknown placeholders in a templated path."""
    for _, field_name, _, _ in string.Formatter().parse(template):
        if field_name is None or field_name == "":
            continue
        if field_name not in _ALLOWED_PLACEHOLDERS:
            raise PathResolverError(
                f"env '{env}' key '{key}' uses unknown placeholder "
                f"'{{{field_name}}}'. Allowed: "
                f"{sorted(_ALLOWED_PLACEHOLDERS)}"
            )


def _safe_format(template: str, substitutions: Dict[str, Optional[str]]) -> str:
    """Substitute placeholders, raising on missing required values."""
    used: Iterable[str] = (
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name
    )
    missing = [name for name in used if substitutions.get(name) is None]
    if missing:
        raise PathResolverError(
            f"missing required substitution(s) {sorted(set(missing))} for "
            f"template {template!r}"
        )
    safe = {k: ("" if v is None else v) for k, v in substitutions.items()}
    return template.format(**safe)
