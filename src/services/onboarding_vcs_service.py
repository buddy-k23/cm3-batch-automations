"""Onboarding VCS service — git/gh orchestration for the open-MR flow (S18-3).

This module owns the *side-effecting* version-control orchestration that
backs the Source Editor UI's "Open MR" button. It was extracted out of
``src/api/routers/onboarding.py`` in S18-3 (#429) so the router endpoint
stays a thin delegator (parse request -> call service -> return response)
and the branch/commit/push/open-PR pipeline becomes unit-testable in
isolation.

The pipeline drives the local ``git`` working tree plus the GitHub
``gh`` CLI, in order:

1. ``git checkout -b <branch_name>`` from the current HEAD.
2. Materialise every planned artefact at its canonical repo-relative path.
3. ``git add -- <paths>`` (only the paths we wrote).
4. ``git commit -m <conventional message>``.
5. ``git rev-parse HEAD`` to capture the new commit SHA.
6. ``git push -u origin <branch_name>``.
7. ``gh pr create --title ... --body ... --head <branch_name>``.

Subprocess safety (architecture principle #2): every invocation goes
through :func:`run_git_or_gh`, which always passes an argument array with
``shell`` disabled. The ``# nosec`` justifications are preserved verbatim
from the original router code; the ``tests/unit/test_no_shell_true.py``
guardrail scans this module too.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404 -- arg-array invocation only (shell disabled)
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException, status


def _display_path(plan_path: Path, cwd: Path) -> str:
    """Coerce a planner-absolute path to the canonical repo-relative form.

    Mirrors the router's display-path logic so the materialised artefact
    layout matches the preview / download-zip surfaces exactly.

    Args:
        plan_path: The absolute on-disk path produced by the planner.
        cwd: The current working directory (output root).

    Returns:
        A POSIX-style relative path when the plan path sits under the
        cwd, else the absolute path string as a defensive fallback.
    """
    try:
        return plan_path.resolve().relative_to(cwd).as_posix()
    except ValueError:
        return plan_path.as_posix()


def default_branch_name(source_code: str) -> str:
    """Synthesize a branch name when the request didn't supply one.

    Uses a UTC timestamp so back-to-back invocations don't collide on
    a single source.

    Args:
        source_code: The workbook's source code (e.g. ``"SHAW"``).

    Returns:
        A branch name of the form
        ``valdo-onboarding/<source>-<YYYYMMDD-HHMMSS>``.
    """
    safe_source = re.sub(r"[^A-Za-z0-9_-]", "_", source_code) or "source"
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"valdo-onboarding/{safe_source}-{stamp}"


def run_git_or_gh(
    cmd: List[str], *, cwd: Path
) -> "subprocess.CompletedProcess[str]":
    """Invoke a ``git`` or ``gh`` command and return the completed process.

    Centralises the ``shell=False`` / arg-array invocation so the
    EC-S11 ``test_no_shell_true`` guardrail stays satisfied. Caller
    inspects the returncode + stdout + stderr.

    Args:
        cmd: The full argv array. The first element is the binary
            (``"git"`` / ``"gh"``).
        cwd: Working directory for the subprocess.

    Returns:
        The :class:`subprocess.CompletedProcess` from
        :func:`subprocess.run`.
    """
    return subprocess.run(  # nosec B603 -- arg-array, no shell
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def open_mr_pipeline(
    plans: List[Any],
    *,
    source_code: str,
    mr_title: str,
    mr_description: str,
    branch_name: str,
    repo_root: Path,
) -> Dict[str, Any]:
    """Run the branch / commit / push / PR pipeline against the local repo.

    Side-effects, in order:

    1. ``git checkout -b <branch_name>`` from the current HEAD.
    2. Materialise every planned artefact at its canonical
       repo-relative path under ``repo_root``.
    3. ``git add`` the materialised paths.
    4. ``git commit`` with the conventional message.
    5. ``git push -u origin <branch_name>``.
    6. ``gh pr create --title ... --body ... --head <branch_name>``.

    Each step's stdout + stderr is captured and surfaced in the
    returned dict on failure so the UI can render a clear error.

    Args:
        plans: Ordered list of planner ``_PlannedWrite`` objects.
        source_code: The workbook's source code (for the default
            branch name).
        mr_title: The PR title from the form.
        mr_description: The PR description from the form.
        branch_name: The resolved branch name (caller-provided or
            synthesised).
        repo_root: The working tree to operate on. In production this
            is :func:`Path.cwd`; tests redirect this via
            ``monkeypatch.chdir`` so the real repo is never touched.

    Returns:
        ``{"pr_url": ..., "branch_name": ..., "commit_sha": ...}``
        on success.

    Raises:
        HTTPException: 500 on any subprocess failure, with the failing
            step + captured output surfaced in ``detail``.
    """
    # 1. Branch from the current HEAD.
    create_branch = run_git_or_gh(
        ["git", "checkout", "-b", branch_name], cwd=repo_root
    )
    if create_branch.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"git checkout -b {branch_name} failed: "
                f"{create_branch.stderr.strip() or create_branch.stdout.strip()}"
            ),
        )

    # 2. Materialise every planned artefact on the new branch.
    written_paths: List[str] = []
    cwd = Path.cwd()
    for plan in plans:
        rel_path = _display_path(plan.path, cwd)
        on_disk = repo_root / rel_path
        on_disk.parent.mkdir(parents=True, exist_ok=True)
        on_disk.write_text(plan.content, encoding="utf-8")
        written_paths.append(rel_path)

    # 3. Stage only the paths we wrote — we never want a stray
    # ``git add -A`` to pick up an unrelated working-tree change.
    add = run_git_or_gh(
        ["git", "add", "--"] + written_paths, cwd=repo_root
    )
    if add.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"git add failed: {add.stderr.strip() or add.stdout.strip()}"
            ),
        )

    # 4. Commit. The conventional message keeps the changelog parser
    # happy and signals the commit's provenance.
    commit_message = (
        f"feat(onboarding): {mr_title} via Source Editor UI\n\n"
        f"{mr_description}"
    )
    commit = run_git_or_gh(
        ["git", "commit", "-m", commit_message], cwd=repo_root
    )
    if commit.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"git commit failed: "
                f"{commit.stderr.strip() or commit.stdout.strip()}"
            ),
        )

    # 5. Resolve the new commit SHA so we can surface it to the UI.
    rev_parse = run_git_or_gh(
        ["git", "rev-parse", "HEAD"], cwd=repo_root
    )
    if rev_parse.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"git rev-parse HEAD failed: "
                f"{rev_parse.stderr.strip() or rev_parse.stdout.strip()}"
            ),
        )
    commit_sha = rev_parse.stdout.strip()

    # 6. Push the branch upstream.
    push = run_git_or_gh(
        ["git", "push", "-u", "origin", branch_name], cwd=repo_root
    )
    if push.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"git push failed: "
                f"{push.stderr.strip() or push.stdout.strip()}"
            ),
        )

    # 7. Open the PR via the gh CLI. The body is fed via --body so a
    # multi-line description doesn't get mangled by argv quoting on
    # any future shell-wrapped invocation.
    gh_create = run_git_or_gh(
        [
            "gh",
            "pr",
            "create",
            "--title",
            mr_title,
            "--body",
            mr_description,
            "--head",
            branch_name,
        ],
        cwd=repo_root,
    )
    if gh_create.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"gh pr create failed: "
                f"{gh_create.stderr.strip() or gh_create.stdout.strip()}"
            ),
        )

    pr_url = gh_create.stdout.strip().splitlines()[-1] if gh_create.stdout else ""
    return {
        "pr_url": pr_url,
        "branch_name": branch_name,
        "commit_sha": commit_sha,
    }


__all__ = ["default_branch_name", "open_mr_pipeline", "run_git_or_gh"]
