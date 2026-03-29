"""CLI command handler for generating multi-record YAML configuration files.

Supports two modes:

* **Non-interactive** — all parameters supplied via CLI flags; writes the YAML
  directly without prompting the user.
* **Interactive** — when ``discriminator`` or ``types`` are absent, guides the
  user through selecting mappings, entering discriminator details, and
  (optionally) assigning cross-type rules.

The public entry point is :func:`run_generate_multi_record_command`.  All
helper functions are intentionally small and independently testable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Public helpers (tested directly)
# ---------------------------------------------------------------------------

def _find_matching_rules(mapping_stem: str, rules_dir: str) -> Optional[Path]:
    """Locate a rules JSON file that corresponds to a mapping file stem.

    Two patterns are tried in order:

    1. ``{mapping_stem}_rules.json``  (e.g. ``header_mapping_rules.json``)
    2. ``{base}_rules.json`` where ``base`` strips a trailing ``_mapping``
       suffix (e.g. ``header_rules.json`` for ``header_mapping``).

    Args:
        mapping_stem: Stem of the mapping filename (no extension).
        rules_dir: Directory to search for rules files.

    Returns:
        Path to the matching rules file, or ``None`` if none is found.
    """
    rules_path = Path(rules_dir) / f"{mapping_stem}_rules.json"
    if rules_path.exists():
        return rules_path

    base = mapping_stem.replace("_mapping", "")
    rules_path = Path(rules_dir) / f"{base}_rules.json"
    if rules_path.exists():
        return rules_path

    return None


def _parse_discriminator(discriminator_str: str) -> Dict:
    """Parse a ``FIELD:POSITION:LENGTH`` discriminator string into a dict.

    Args:
        discriminator_str: String in the form ``"FIELD_NAME:POSITION:LENGTH"``
            where POSITION and LENGTH are positive integers.

    Returns:
        Dict with keys ``field`` (str), ``position`` (int), ``length`` (int).

    Raises:
        ValueError: When the string does not have exactly three colon-separated
            segments or POSITION/LENGTH are not integers.
    """
    parts = discriminator_str.split(":")
    if len(parts) != 3:
        raise ValueError(
            f"Discriminator must be in FIELD:POSITION:LENGTH format, got: {discriminator_str!r}"
        )
    field, position_str, length_str = parts
    try:
        position = int(position_str)
        length = int(length_str)
    except ValueError:
        raise ValueError(
            f"POSITION and LENGTH must be integers, got: {position_str!r}, {length_str!r}"
        )
    return {"field": field, "position": position, "length": length}


def _parse_type_string(type_str: str):
    """Parse a ``CODE=MAPPING_NAME`` type-mapping string.

    The code portion may include a position qualifier separated by a colon,
    e.g. ``header:first=batch_header`` means code ``header:first`` maps to
    mapping ``batch_header``.

    Args:
        type_str: String in the form ``"CODE=MAPPING_NAME"``.

    Returns:
        Tuple of ``(code, mapping_name)`` as strings.

    Raises:
        ValueError: When the string does not contain an ``=`` sign.
    """
    if "=" not in type_str:
        raise ValueError(
            f"Type mapping must be in CODE=MAPPING_NAME format, got: {type_str!r}"
        )
    code, mapping_name = type_str.split("=", 1)
    return code.strip(), mapping_name.strip()


def _write_yaml(
    output_path: str,
    discriminator: Dict,
    record_types: Dict,
    cross_type_rules: Optional[List[Dict]],
) -> None:
    """Write a multi-record YAML config to disk.

    Parent directories are created automatically.

    Args:
        output_path: Destination file path (including filename).
        discriminator: Dict with keys ``field``, ``position``, ``length``.
        record_types: Mapping of type-name → record-type config dict.
        cross_type_rules: List of cross-type rule dicts, or ``None`` to omit
            the ``cross_type_rules`` key entirely.
    """
    import yaml

    config: Dict = {
        "multi_record": {
            "discriminator": discriminator,
            "record_types": record_types,
            "default_action": "warn",
        }
    }
    if cross_type_rules:
        config["multi_record"]["cross_type_rules"] = cross_type_rules

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        yaml.dump(config, fh, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Type-string → record_types dict entry
# ---------------------------------------------------------------------------

def _build_record_type_entry(
    code: str,
    mapping_name: str,
    mappings_dir: str,
    rules_dir: str,
) -> Dict:
    """Build a record-type config dict for one code/mapping pair.

    If a rules file matching the mapping is found automatically, it is
    included in the entry.

    The code may optionally embed a position qualifier using a colon, e.g.
    ``header:first`` sets ``match=""`` and ``position="first"``.

    Args:
        code: Discriminator code value (e.g. ``"HDR"``).  May contain a
            ``:first`` or ``:last`` qualifier.
        mapping_name: Stem of the mapping JSON file (without ``.json``).
        mappings_dir: Directory containing mapping JSON files.
        rules_dir: Directory to search for matching rules files.

    Returns:
        Dict suitable for use as a ``record_types`` entry in the YAML config.
    """
    entry: Dict = {}

    # Handle position qualifier (e.g. "header:first")
    if ":" in code:
        name_part, position_qualifier = code.rsplit(":", 1)
        entry["position"] = position_qualifier
        # match left empty when using position
    else:
        entry["match"] = code

    mapping_path = str(Path(mappings_dir) / f"{mapping_name}.json")
    entry["mapping"] = mapping_path
    entry["expect"] = "any"

    rules_path = _find_matching_rules(mapping_name, rules_dir)
    if rules_path:
        entry["rules"] = str(rules_path)

    return entry


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def run_interactive_mode(output: str, mappings_dir: str, rules_dir: str) -> None:
    """Guide the user interactively to build a multi-record YAML config.

    Guides the user through six steps: listing and selecting mapping files,
    collecting discriminator field details, assigning a discriminator code to
    each mapping, optionally attaching auto-matched rules, optionally adding a
    ``required_companion`` cross-type rule, and finally writing the YAML.

    Args:
        output: Destination YAML file path.
        mappings_dir: Directory containing mapping JSON files.
        rules_dir: Directory to search for matching rules files.
    """
    import click

    mappings = sorted(Path(mappings_dir).glob("*.json"))
    if not mappings:
        click.echo(click.style(f"No mapping files found in {mappings_dir}", fg="red"))
        return

    click.echo("\nAvailable mappings:")
    for i, m in enumerate(mappings, 1):
        click.echo(f"  {i}. {m.stem}")

    selection = input("\nSelect mappings (comma-separated numbers or 'all') [all]: ").strip() or "all"

    if selection.lower() == "all":
        selected_mappings = mappings
    else:
        try:
            indices = [int(s.strip()) - 1 for s in selection.split(",")]
            selected_mappings = [mappings[i] for i in indices]
        except (ValueError, IndexError):
            click.echo(click.style("Invalid selection.", fg="red"))
            return

    # Discriminator
    field = input("\nDiscriminator field name: ").strip()
    position = int(input("Discriminator start position (1-indexed): ").strip())
    length = int(input("Discriminator field length (characters): ").strip())
    discriminator = {"field": field, "position": position, "length": length}

    # Record types
    record_types: Dict = {}
    for mapping in selected_mappings:
        click.echo(f"\n  Mapping: {mapping.stem}")
        code = input(f"    Discriminator code (or 'first'/'last' for position-based): ").strip()

        entry: Dict = {}
        if code in ("first", "last"):
            entry["position"] = code
        else:
            entry["match"] = code
        entry["mapping"] = str(mapping)
        entry["expect"] = "any"

        rules_path = _find_matching_rules(mapping.stem, rules_dir)
        if rules_path:
            use_rules = input(f"    Found rules {rules_path.name}. Use it? [Y/n]: ").strip().lower()
            if use_rules in ("", "y", "yes"):
                entry["rules"] = str(rules_path)

        type_key = mapping.stem.replace("_mapping", "")
        record_types[type_key] = entry

    # Optional cross-type rules
    cross_type_rules: Optional[List[Dict]] = None
    add_rules = input("\nAdd a required_companion cross-type rule? [y/N]: ").strip().lower()
    if add_rules in ("y", "yes"):
        when_type = input("  When type (e.g. 'header'): ").strip()
        requires_type = input("  Requires type (e.g. 'detail'): ").strip()
        severity = input("  Severity [error]: ").strip() or "error"
        cross_type_rules = [
            {
                "check": "required_companion",
                "when_type": when_type,
                "requires_type": requires_type,
                "severity": severity,
            }
        ]

    _write_yaml(output, discriminator, record_types, cross_type_rules)
    click.echo(click.style(f"\nGenerated: {output}", fg="green"))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_generate_multi_record_command(
    output: str,
    discriminator: Optional[str],
    types: Optional[List[str]],
    rules_dir: str = "config/rules",
    mappings_dir: str = "config/mappings",
) -> None:
    """Generate a multi-record YAML config — non-interactive or interactive.

    When ``discriminator`` and ``types`` are both provided the YAML is written
    immediately without any prompts.  When either is absent the user is guided
    through the interactive wizard.

    Args:
        output: Destination YAML file path.
        discriminator: Discriminator spec as ``"FIELD:POSITION:LENGTH"``, or
            ``None`` to trigger interactive mode.
        types: List of ``"CODE=MAPPING_NAME"`` strings, or ``None`` / empty
            list to trigger interactive mode.
        rules_dir: Directory to search for matching rules files.
        mappings_dir: Directory containing mapping JSON files.

    Raises:
        ValueError: When ``discriminator`` cannot be parsed.
    """
    if not discriminator or not types:
        run_interactive_mode(output, mappings_dir, rules_dir)
        return

    disc_dict = _parse_discriminator(discriminator)

    record_types: Dict = {}
    for type_str in types:
        code, mapping_name = _parse_type_string(type_str)

        entry = _build_record_type_entry(code, mapping_name, mappings_dir, rules_dir)

        # Determine the dict key: strip position qualifier from code if present
        if ":" in code:
            type_key = code.split(":")[0]
        else:
            type_key = code

        record_types[type_key] = entry

    _write_yaml(output, disc_dict, record_types, cross_type_rules=None)

    import click
    click.echo(click.style(f"Generated: {output}", fg="green"))
