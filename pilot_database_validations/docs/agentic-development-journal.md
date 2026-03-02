# Agentic Development Journal (Process Guide)

## Purpose
This document defines how every step of agent-led development is documented so future teams can replay decisions, understand why changes were made, and troubleshoot quickly.

## Where logs live
- Folder: `logs/agentic-development/`
- File pattern: `YYYY-MM-DD-step-log.md`

## Logging rule (mandatory)
Every meaningful action must be logged:
- planning decisions
- rule/schema changes
- code implementation changes
- test runs and outcomes
- approval decisions
- rollbacks/rework

## Standard Step Entry Template

```md
## Step <N> - <Short Title>
- Timestamp:
- Stage: PLANNING | CONTRACT_FREEZE | BUILD | INTEGRATE | HARDEN | RELEASE
- Agent:
- Branch/Worktree:
- Inputs:
- Action taken:
- Files changed:
- Validation performed:
- Result:
- Risks/assumptions:
- Requires approval? (Y/N)
- Approval status:
- Next step:
```

## Decision Record Rule
If architecture, rule semantics, or precedence changes, create/update:
- `docs/adrs/ADR-*.md`

Each ADR must capture:
- context
- decision
- alternatives considered
- impact
- rollback strategy

## Test Evidence Rule
For each implementation step include:
- command/query used
- dataset used
- pass/fail counts
- artifact paths

## Traceability Matrix
Maintain a table mapping:
- requirement -> rule ID -> test case -> report evidence

## Daily Closeout Checklist
- [ ] all steps logged
- [ ] open risks captured
- [ ] blockers identified
- [ ] next-day start point documented

## Future Reuse Guidance
Before starting new enhancements:
1. read latest step logs
2. read relevant ADRs
3. read rule governance and workflow docs
4. reuse existing rule packs before adding new DSL behavior
