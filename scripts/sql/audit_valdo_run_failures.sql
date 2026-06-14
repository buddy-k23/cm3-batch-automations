-- =============================================================================
-- AUDIT.VALDO_RUN_FAILURES
--
-- Append-only audit sink for the Valdo E2E batch testing harness.
-- One row per failed gate per (env, source, run_id).
--
-- Hard rules (enforced operationally, not by triggers):
--   * INSERT only. Never UPDATE or DELETE rows. The table is the audit
--     trail for batch regressions and must be preservable verbatim.
--   * Writers are wrapper scripts under scripts/ — Valdo internals never
--     touch this table.
--   * Schema is intentionally separate from staging (STG_*) and from any
--     Java-batch schema so that grants on the audit trail can be scoped
--     independently.
--
-- Deployed by: dba, once per environment (sit, ait).
-- Referenced by: scripts/e2e_lib/failure_sink.py
-- =============================================================================

CREATE TABLE AUDIT.VALDO_RUN_FAILURES (
    failure_id        NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id            VARCHAR2(64)  NOT NULL,
    env               VARCHAR2(8)   NOT NULL,            -- 'sit' | 'ait'
    source            VARCHAR2(64)  NOT NULL,
    pipeline_name     VARCHAR2(128) NOT NULL,
    gate_name         VARCHAR2(128) NOT NULL,
    gate_stage        VARCHAR2(32),                       -- 'input' | 'output' | etc.
    layer             VARCHAR2(8),                        -- 'L1' | 'L2' | 'L3' | NULL
    file_name         VARCHAR2(256),
    file_type         VARCHAR2(64),
    mapping_path      VARCHAR2(512),
    mapping_version   VARCHAR2(32),
    error_type        VARCHAR2(64)  NOT NULL,             -- 'structural' | 'compare_diff' | 'threshold_breach' | 'java_step_failed' | etc.
    error_count       NUMBER        DEFAULT 0,
    row_count         NUMBER,
    report_path       VARCHAR2(512),
    blocking          NUMBER(1)     DEFAULT 1,            -- 1 = halted source, 0 = recorded only
    failed_at         TIMESTAMP     DEFAULT SYSTIMESTAMP,
    failure_detail    CLOB                                -- short JSON: first N error rows / message
);

CREATE INDEX IDX_VRF_RUN ON AUDIT.VALDO_RUN_FAILURES (run_id);
CREATE INDEX IDX_VRF_SRC ON AUDIT.VALDO_RUN_FAILURES (env, source, failed_at);
