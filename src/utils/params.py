from __future__ import annotations

import re
import uuid
from datetime import date, timedelta


def resolve_params(template: str, params: dict) -> str:
    """Substitute ${variable} placeholders in a string.

    Built-in variables (always available):
      ${today}       - today's date as YYYYMMDD
      ${yesterday}   - yesterday's date as YYYYMMDD
      ${run_id}      - UUID for this run (same value within a suite run)
      ${environment} - value from params or empty string

    Raises ValueError if any ${variable} remains unresolved after substitution.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)

    today_str = today.strftime("%Y%m%d")
    builtins = {
        "today": today_str,
        "yesterday": yesterday.strftime("%Y%m%d"),
        "run_id": str(uuid.uuid4()),
        "environment": "",
        "run_date": today_str,  # same as ${today} — YYYYMMDD — user can override via --params run_date=...
    }

    # Caller-supplied params override builtins (except run_id which is generated
    # once and passed in via params to keep it consistent within a suite run).
    merged = {**builtins, **params}

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        if var_name in merged:
            return str(merged[var_name])
        # Leave unresolved for the post-pass check.
        return match.group(0)

    result = re.sub(r"\$\{([^}]+)\}", replacer, template)

    # Check for any remaining unresolved placeholders.
    remaining = re.findall(r"\$\{([^}]+)\}", result)
    if remaining:
        raise ValueError(
            f"Unresolved parameter placeholder(s): {', '.join('${' + v + '}' for v in remaining)}"
        )

    return result
