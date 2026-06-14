"""Shared helpers for the Valdo E2E batch testing harness.

Renamed from ``scripts/lib`` to ``scripts/e2e_lib`` because the repo's
``.gitignore`` excludes any directory named ``lib/`` (a common Python build
artifact pattern). Functionality is unchanged.

Only thin, dependency-free utilities live here. Anything that needs to reach
into Valdo internals belongs in ``src/`` instead.
"""
