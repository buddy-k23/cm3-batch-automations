"""Unit tests for the onboarding VCS service (S18-3, #429).

The git/gh subprocess orchestration that previously lived inline in
``src/api/routers/onboarding.py`` now lives in
:mod:`src.services.onboarding_vcs_service`. These tests exercise the
moved pipeline directly at the service layer (mocked subprocess) — the
endpoint-level parity is covered by
``tests/integration/test_api_onboarding.py``.
"""

from __future__ import annotations

import subprocess  # nosec B404 -- test helper builds CompletedProcess fakes only
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.services import onboarding_vcs_service as vcs


@dataclass
class _FakePlan:
    """Minimal stand-in for the planner's ``_PlannedWrite`` object."""

    path: Path
    content: str


def _make_plan(repo_root: Path, rel: str, content: str) -> _FakePlan:
    """Build a fake plan whose path resolves under ``Path.cwd()``."""
    return _FakePlan(path=(Path.cwd() / rel).resolve(), content=content)


# ---------------------------------------------------------------------------
# default_branch_name
# ---------------------------------------------------------------------------


def test_default_branch_name_uses_safe_source_and_prefix():
    name = vcs.default_branch_name("SHAW")
    assert name.startswith("valdo-onboarding/SHAW-")


def test_default_branch_name_sanitises_unsafe_source():
    name = vcs.default_branch_name("a/b c!")
    # All non [A-Za-z0-9_-] chars collapse to underscores.
    assert name.startswith("valdo-onboarding/a_b_c_-")


def test_default_branch_name_blank_source_falls_back():
    # An empty source code sanitises to "" which falls back to "source".
    name = vcs.default_branch_name("")
    assert name.startswith("valdo-onboarding/source-")


# ---------------------------------------------------------------------------
# run_git_or_gh — arg-array, shell-less invocation
# ---------------------------------------------------------------------------


def test_run_git_or_gh_passes_arg_array_no_shell(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(vcs.subprocess, "run", fake_run)
    result = vcs.run_git_or_gh(["git", "status"], cwd=tmp_path)

    assert captured["cmd"] == ["git", "status"]
    # Never shell=True.
    assert "shell" not in captured["kwargs"] or captured["kwargs"]["shell"] is False
    assert captured["kwargs"].get("capture_output") is True
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# open_mr_pipeline — happy path
# ---------------------------------------------------------------------------


def test_open_mr_pipeline_runs_full_flow_and_returns_contract(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)

    invocations: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        invocations.append(list(cmd))
        stdout = ""
        if cmd[0] == "git" and cmd[1] == "rev-parse":
            stdout = "deadbeef0000000000000000000000000000abcd\n"
        elif cmd[0] == "gh" and cmd[1] == "pr":
            stdout = "https://github.com/example/repo/pull/7\n"
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(vcs.subprocess, "run", fake_run)

    plans = [_make_plan(repo_dir, "config/e2e/sources/SHAW.yml", "source: SHAW\n")]
    result = vcs.open_mr_pipeline(
        plans,
        source_code="SHAW",
        mr_title="add SHAW source",
        mr_description="Body line one\nBody line two",
        branch_name="valdo-onboarding/SHAW-test",
        repo_root=repo_dir,
    )

    assert result == {
        "pr_url": "https://github.com/example/repo/pull/7",
        "branch_name": "valdo-onboarding/SHAW-test",
        "commit_sha": "deadbeef0000000000000000000000000000abcd",
    }
    # Artefact was materialised on disk under the repo root.
    written = repo_dir / "config/e2e/sources/SHAW.yml"
    assert written.is_file()
    assert written.read_text(encoding="utf-8") == "source: SHAW\n"

    cmds = [tuple(c) for c in invocations]
    assert ("git", "checkout", "-b", "valdo-onboarding/SHAW-test") in cmds
    assert any(c[0] == "git" and c[1] == "add" for c in cmds)
    assert any(c[0] == "git" and c[1] == "commit" and "-m" in c for c in cmds)
    assert any(c[0] == "git" and c[1] == "push" for c in cmds)
    gh = [c for c in cmds if c[0] == "gh" and c[1] == "pr"][0]
    assert gh[gh.index("--title") + 1] == "add SHAW source"
    assert gh[gh.index("--body") + 1] == "Body line one\nBody line two"
    assert gh[gh.index("--head") + 1] == "valdo-onboarding/SHAW-test"


# ---------------------------------------------------------------------------
# open_mr_pipeline — failure surfaces as 500 with the failing step
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fail_on, expected_fragment",
    [
        (("git", "checkout"), "git checkout -b"),
        (("git", "add"), "git add failed"),
        (("git", "commit"), "git commit failed"),
        (("git", "rev-parse"), "git rev-parse HEAD failed"),
        (("git", "push"), "git push failed"),
        (("gh", "pr"), "gh pr create failed"),
    ],
)
def test_open_mr_pipeline_failure_raises_500(monkeypatch, tmp_path, fail_on, expected_fragment):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)

    def fake_run(cmd, *args, **kwargs):
        if (cmd[0], cmd[1]) == fail_on:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="boom")
        stdout = ""
        if cmd[0] == "git" and cmd[1] == "rev-parse":
            stdout = "abc123\n"
        elif cmd[0] == "gh" and cmd[1] == "pr":
            stdout = "https://example/pull/1\n"
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(vcs.subprocess, "run", fake_run)

    plans = [_make_plan(repo_dir, "config/e2e/sources/SHAW.yml", "source: SHAW\n")]
    with pytest.raises(HTTPException) as exc:
        vcs.open_mr_pipeline(
            plans,
            source_code="SHAW",
            mr_title="t",
            mr_description="d",
            branch_name="valdo-onboarding/SHAW-test",
            repo_root=repo_dir,
        )
    assert exc.value.status_code == 500
    assert expected_fragment in exc.value.detail
