# CI E2E Playwright Design

**Date:** 2026-03-04
**Issue:** #47

## Goal

Run `scripts/e2e_full_ui.py` automatically on every push to any branch. Failures block merges (required status check).

## Architecture

Add a single `e2e` job to the existing `.github/workflows/ci.yml`. The job starts the FastAPI server in the background with uvicorn, waits for it to be healthy, then runs the Playwright suite. Screenshots are uploaded as artifacts on failure for debugging.

No Oracle env vars are needed — the four E2E workflows (quick_test, recent_runs, mapping_generator, api_tester) do not touch the database, and the server starts successfully without Oracle credentials.

## Job Design

**Trigger:** Every push to any branch (inherits `ci.yml` trigger).

**Ordering:** `needs: test-and-docs` — E2E runs after unit tests pass.

**Steps:**
1. Checkout + Python 3.11
2. `pip install -r requirements-dev.txt && pip install -e .`
3. `playwright install chromium --with-deps`
4. `uvicorn src.api.main:app --host 127.0.0.1 --port 8000 &`
5. Retry loop: poll `GET /api/v1/system/health` up to 10 times (0.5 s apart), fail if server never responds
6. `python3 scripts/e2e_full_ui.py` (exits non-zero on any FAIL step)
7. `actions/upload-artifact@v4` with `if: failure()` — uploads `screenshots/e2e-full-*/`

## Files Changed

| File | Change |
|------|--------|
| `.github/workflows/ci.yml` | Add `e2e` job |
| `requirements-dev.txt` | Add `playwright>=1.40.0` |
