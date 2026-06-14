"""Pydantic models for suite-based scheduled/triggered validation runs."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class StepDefinition(BaseModel):
    """A single validation or comparison step within a suite.

    Attributes:
        name: Human-readable label for the step.
        type: Step execution type — ``"validate"`` or ``"compare"``.
        file_pattern: Glob pattern or explicit file path to process.
        mapping: Optional path to the mapping JSON config file.
        rules: Optional path to the rules config JSON file.
    """

    name: str
    type: Literal["validate", "compare"]
    file_pattern: str
    mapping: Optional[str] = None
    rules: Optional[str] = None


class NotificationTarget(BaseModel):
    """A single notification destination (email, Teams, or Slack).

    Attributes:
        type: Channel type — ``"email"``, ``"teams"``, or ``"slack"``.
        to: List of recipient email addresses (used when type is ``"email"``).
        url: Incoming webhook URL (used when type is ``"teams"`` or ``"slack"``).
    """

    type: Literal["email", "teams", "slack"]
    to: Optional[List[str]] = None
    url: Optional[str] = None


class NotificationsConfig(BaseModel):
    """Notification routing for suite results.

    Attributes:
        on_failure: Targets to notify when the suite fails.
        on_success: Targets to notify when the suite passes.
    """

    on_failure: List[NotificationTarget] = Field(default_factory=list)
    on_success: List[NotificationTarget] = Field(default_factory=list)


class SuiteDefinition(BaseModel):
    """Top-level model for a named suite of validation steps.

    Suites are loaded from YAML files in ``config/suites/``.  Each suite
    may declare an optional human-readable description and a flat dict of
    threshold overrides that apply to every step unless the step specifies
    its own thresholds.

    Attributes:
        name: Unique machine-readable suite name (used as CLI/API identifier).
        description: Optional human-readable description of this suite.
        steps: Ordered list of :class:`StepDefinition` objects to execute.
        thresholds: Optional dict of threshold values (e.g. ``{"max_errors": 5}``).
            Treated as raw metadata — enforcement is handled by the caller.
        notifications: Optional notification configuration. When present,
            the scheduler service sends notifications after suite completion.
    """

    name: str
    description: Optional[str] = None
    steps: List[StepDefinition] = Field(default_factory=list)
    thresholds: Optional[Dict] = None
    notifications: Optional[NotificationsConfig] = None
