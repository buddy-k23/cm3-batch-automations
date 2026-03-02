# Rule Authoring Workflow

## Objective
Allow agents to add/update rules safely so the framework remains generic and deterministic.

## Workflow

1. **Propose**
   - Create/update rule in `rules/*.json`
   - Include scenario reference and rationale

2. **Schema Check**
   - Validate against `schemas/rules.schema.json`
   - Reject unknown operators or invalid scopes

3. **Static Rule Checks**
   - Detect circular dependencies
   - Detect conflicting priorities
   - Detect unreachable conditions

4. **Test Binding**
   - Attach at least one positive and one negative test case
   - Map rule to dataset in `tests/golden/`

5. **Dry Run**
   - Generate validation result and telemetry for sample data
   - Confirm deterministic output hash

6. **Review**
   - Rule Validator Agent reviews syntax and semantics
   - QA Agent reviews failure modes

7. **Approval Gate**
   - Human approval required for:
     - new rule types
     - severity changes
     - precedence/order changes

8. **Promote**
   - For extracted candidates, run Wave 5 governance promotion (`tools/rule_promotion.py`) with explicit decisions + conflict checks
   - Mark rule pack version
   - Update changelog and migration notes

---

## Required Rule Metadata (minimum)
- `ruleId`
- `name`
- `scope` (`field`, `record`, `group`, `file`)
- `severity` (`ERROR`, `WARN`, `INFO`)
- `condition` / `expression`
- `messageTemplate`
- `appliesTo`
- `priority`
- `version`
- `owner`

---

## Recommended Validation Order
1. File-level structural checks
2. Mapping/contract checks
3. Field-level checks
4. Cross-field checks
5. Group-level checks (account/contact sequences)
6. Header/trailer reconciliation

---

## Change Control
Any rule change must include:
- before/after behavior example
- impact on existing rule packs
- explicit compatibility status: `compatible` | `breaking`
