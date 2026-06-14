# DB Profiles Dropdown — Design Spec

**Date:** 2026-04-02
**Status:** Approved

---

## Overview

Allow operators to define multiple named database connections on the backend. The DB Compare tab presents a dropdown of these profiles; selecting one uses the server-side credentials — the password never reaches the browser.

---

## Config File

**Path:** `config/db_connections.yaml`

```yaml
connections:
  - name: "Production Oracle"
    adapter: oracle
    host: "prod-db:1521/PRODPDB1"
    user: "APP_INT"
    schema: "APP_INT"
    password_env: "DB_PROD_PASSWORD"

  - name: "Staging Oracle"
    adapter: oracle
    host: "staging-db:1521/STAGPDB1"
    user: "APP_INT"
    schema: "APP_INT"
    password_env: "DB_STAGING_PASSWORD"

  - name: "Local Dev"
    adapter: oracle
    host: "localhost:1521/FREEPDB1"
    user: "APP_INT"
    schema: "APP_INT"
    password_env: "ORACLE_PASSWORD"
```

**Rules:**
- `password_env` is the **name** of an environment variable. The password value is never stored in the file.
- The file is optional. If absent or empty, only "Custom…" appears in the dropdown.
- Profiles whose `password_env` is not set in the environment are still listed but marked with `password_env_set: false`.

---

## Backend

### New service: `src/services/db_profiles_service.py`

- `load_profiles(path: Path) -> list[DbProfile]` — reads `config/db_connections.yaml`; returns a list of `DbProfile` dataclasses. Password value never included.
- `resolve_profile(name: str) -> DbConfig` — looks up profile by name, reads `os.environ[password_env]`, returns a fully populated `DbConfig`. Raises `KeyError` if profile name not found; raises `RuntimeError` if the env var is missing.

### New model: `DbProfile`

Added to `src/api/models/` (new file `db_profile.py`):

| Field | Type | Notes |
|-------|------|-------|
| `name` | `str` | Display name shown in dropdown |
| `adapter` | `str` | `oracle`, `postgresql`, or `sqlite` |
| `host` | `str` | Host/DSN string |
| `user` | `str` | Database username |
| `schema` | `str` | Schema qualifier |
| `password_env` | `str` | Name of the env var holding the password |
| `password_env_set` | `bool` | Computed: `True` if `os.environ.get(password_env)` is non-empty |

### API changes (`src/api/routers/system.py`)

**New endpoint:**
- `GET /api/v1/system/db-profiles` — returns `{"profiles": [...DbProfile...]}`. No auth required (non-sensitive). Returns `[]` if config file is absent.

**Modified endpoints:**
- `POST /api/v1/system/db-ping` — gains optional `profile_name: str = Form(None)`. If provided, backend resolves the full connection via `resolve_profile`; existing ad-hoc fields (`db_host`, `db_user`, `db_password`, `db_schema`, `db_adapter`) still work when `profile_name` is absent (Custom… path).
- `POST /api/v1/files/db-compare` — same pattern: accepts `profile_name` OR ad-hoc credentials. If `profile_name` is set, ignore any ad-hoc credential fields.

---

## UI Changes (`ui.html`, `ui.js`)

### Profile dropdown

A new **"DB Profile"** row is added to the connection form above the existing manual fields:

```
[ — select a profile —  ▾ ]
```

Options:
1. `— select a profile —` (blank default)
2. One entry per named profile from `GET /api/v1/system/db-profiles`
3. `Custom…` (always last)

Profiles with `password_env_set: false` are shown with a `⚠️` suffix.

### Behaviour

- **On DB Compare tab open** → fetch `GET /api/v1/system/db-profiles`; populate dropdown.
- **Named profile selected** → hide manual fields (adapter/host/user/password/schema); update connection chip text to profile name.
- **"Custom…" selected** → show manual fields as today (existing behaviour unchanged).
- **Test Connection with named profile** → POST `profile_name` only; no credentials in request.
- **Run compare with named profile** → POST `profile_name` to `/api/v1/files/db-compare`; backend resolves password.
- **Profile with `password_env_set: false`** → inline error shown: `"Password env var DB_PROD_PASSWORD is not set on the server"`.

### sessionStorage

Named profile selection is **not** persisted in sessionStorage — no value in caching a server-side name. Custom ad-hoc credentials continue to use sessionStorage as today.

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| `config/db_connections.yaml` missing | API returns `[]`; only "Custom…" in dropdown |
| Profile `password_env` not set | `password_env_set: false` in API response; UI shows `⚠️` and inline error on test/run |
| Profile name not found on server | `db-ping` / `db-compare` return `{"ok": false, "error": "Profile not found: ..."}` |
| YAML parse error | Server logs warning; API returns `[]` |

---

## Testing

- **Unit tests** (`tests/unit/`):
  - `db_profiles_service.py` — load_profiles (file present, absent, malformed), resolve_profile (found, not found, missing env var), `password_env_set` computation
  - `system.py` router — `GET /api/v1/system/db-profiles` (happy path, empty, missing file), `db-ping` with `profile_name`
- **E2E tests** (`tests/e2e/`):
  - Profile dropdown populated on tab open
  - Selecting a profile hides manual fields
  - Selecting "Custom…" shows manual fields
  - `⚠️` shown for profiles with missing env var

---

## Files to Create / Modify

| File | Action |
|------|--------|
| `config/db_connections.yaml` | **Create** — sample with one "Local Dev" profile |
| `src/services/db_profiles_service.py` | **Create** |
| `src/api/models/db_profile.py` | **Create** |
| `src/api/routers/system.py` | **Modify** — add `GET /db-profiles`, update `db-ping` |
| `src/reports/static/ui.html` | **Modify** — add profile dropdown row to connection form |
| `src/reports/static/ui.js` | **Modify** — fetch profiles, populate dropdown, toggle manual fields |
| `tests/unit/services/test_db_profiles_service.py` | **Create** |
| `tests/unit/api/test_system_db_profiles.py` | **Create** |
| `tests/e2e/test_db_compare_profiles.py` | **Create** |
| `docs/USAGE_AND_OPERATIONS_GUIDE.md` | **Modify** — document `config/db_connections.yaml` |
