"""Source-onboarding workbook subsystem.

Public modules:
    workbook_schema: Canonical Excel workbook schema definition + validator.

The onboarding subsystem (epic EC, Sprint 2) lets BAs ship a new source by
filling in a single ``.xlsx`` workbook. Subsequent stories layer a reader
(EC-S2), source-YAML emitter (EC-S3), mapping emitter (EC-S4), rules emitter
(EC-S5), and ``valdo onboard-source`` CLI (EC-S6) on top of the schema
defined here.
"""
