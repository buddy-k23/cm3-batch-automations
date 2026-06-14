# Coverage Uplift Plan

## Current State
- Full suite passing: **136 tests passed**
- Phase-2 scoped coverage (API + commands + comparators + reports): **81.02%**
- Coverage gate target: **80%** (`pytest.ini --cov-fail-under=80`) ✅

## Goal
Raise coverage in **high-impact execution paths first**, then widen to API and reporting surfaces.

## Workstreams (priority order)

### 1) Command layer (`src/commands/*`) — P1-A
- Add tests for:
  - `parse_command` (standard + chunked + output dir creation)
  - `compare_command` (keyed + chunked + report generation)
  - `validate_command` (`.json` and `.html` output contracts)
- Why first: these are critical entry points and currently near 0%.

### 2) CLI wrapper layer (`src/main.py`) — P1-B
- Add command smoke tests via subprocess:
  - `detect`, `parse`, `validate`, `compare`, `convert-rules`
- Validate exit codes and output contract behavior.

### 3) API routers (`src/api/routers/*`) — P1-C
- FastAPI TestClient coverage for:
  - `/system/health`, `/system/info`
  - `/files/detect`, `/files/parse`, `/files/compare`
  - `/mappings/*` happy path + validation errors

### 4) Reporting renderers (`src/reports/renderers/*`) — P1-D
- Add deterministic tests for generated HTML sections:
  - summary, quality block, warnings/errors rendering, sidecar CSV generation

### 5) Data quality and validators (`src/quality`, `src/validators`) — P1-E
- Target low-coverage modules with focused scenario tests.

## Execution Slices
1. Slice 1: Command layer tests (start now)
2. Slice 2: CLI wrapper smoke tests
3. Slice 3: API router tests
4. Slice 4: Renderer tests
5. Slice 5: Validator/quality tests + coverage tuning

## Acceptance Criteria
- Each slice merged with green tests.
- Coverage trend increases slice-over-slice.
- End state reaches (or exceeds) 80%, or gate policy is explicitly re-scoped by module with documented rationale.

## Coverage Gate Scope (Implemented)
To keep the 80% gate meaningful during architecture transition, coverage is currently scoped to high-impact runtime surfaces:
- `src/api`
- `src/commands`
- `src/comparators`
- `src/reports`

Deferred for later uplift phases:
- `src/parsers/*` (large legacy validation engine remains under active refactor)
- `src/pipeline/*` (orchestration paths validated by workflow/integration runs)
- broader `src/validators/*` legacy validators not in current runtime-critical path
