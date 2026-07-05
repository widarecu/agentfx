# US-019: Per-User OANDA Credentials, Encrypted at Rest

**Version:** v0.6.0 — Paper Trading MVP
**Priority:** P0 — Must Have (blocks paper runner deploying under per-user accounts)
**Status:** TODO

## Given-When-Then

**GIVEN** a TRADER user has obtained their own OANDA Practice account (account ID + API key)
  **AND** they navigate to `/settings` in the portal
**WHEN** they submit their credentials via `PUT /api/v1/users/me/credentials`
**THEN** the system:

1. Encrypts the API key at rest using Fernet (from the already-installed `cryptography` library)
2. Persists a row in `user_oanda_credentials` with `oanda_account_id` (plaintext, masked in reads), `oanda_api_key_encrypted` (Fernet ciphertext), `oanda_environment=PRACTICE`, `verified_at=null`
3. Returns a masked view of the credential state (last 4 of account ID, environment, verified status)

  **AND WHEN** the user clicks "Test connection" (`POST /api/v1/users/me/credentials/verify`)
**THEN** the backend decrypts the API key, calls OANDA `/v3/accounts/{id}/summary`; on 200 sets `verified_at=now()` and clears any prior error; on failure stores a sanitised `verify_failure_reason` and returns 400

  **AND** at no point is the plaintext API key logged, cached, or returned in an API response — the decrypted value lives only inside a paper-runner subprocess or the verify handler's stack.

## Acceptance Criteria

### Schema

- [ ] Alembic migration creates `user_oanda_credentials` with columns:
  - `id` UUID primary key
  - `user_id` INT FK → `users.id`, **UNIQUE** (one active credential set per user; edits are upserts)
  - `oanda_account_id` VARCHAR(64) NOT NULL
  - `oanda_api_key_encrypted` BYTEA NOT NULL
  - `oanda_environment` ENUM(`PRACTICE`, `LIVE`) NOT NULL DEFAULT `PRACTICE`
  - `verified_at` TIMESTAMP NULLABLE
  - `verify_failure_reason` VARCHAR(500) NULLABLE
  - `key_version` INT NOT NULL DEFAULT 1 (supports future rotation, no rotation in v0.6.0)
  - `created_at`, `updated_at` TIMESTAMP NOT NULL
- [ ] `oanda_environment=LIVE` is gated behind `settings.LIVE_TRADING_ENABLED=false` (default) — a `PUT` with `environment=LIVE` when the flag is false returns 400 with `"LIVE trading is not yet enabled — v1.0.0"`.

### Encryption module

- [ ] New `src/agentfx/api/core/secrets.py`:
  - `encrypt_secret(plaintext: str, key_version: int = 1) -> bytes`
  - `decrypt_secret(ciphertext: bytes, key_version: int = 1) -> str`
  - Loads Fernet key from `FERNET_KEY` env var; fail-fast at app startup if missing in UAT / staging / production (allowed to be dev-default only in `.env.development`)
  - Never logs plaintext
  - Round-trip unit tests
- [ ] `Dockerfile` and Railway deploy configs updated to inject `FERNET_KEY` from platform secret store
- [ ] `.env.development` includes a dev-only Fernet key with a comment warning against use in higher environments
- [ ] `.env.uat` reads `FERNET_KEY` from the Docker environment (unset in the file so operators must set it explicitly)

### API endpoints

All under `/api/v1/users/me/credentials/` and require `get_current_active_user`:

- [ ] `GET /` — returns `{account_id_masked: "***4567", environment, verified: bool, verified_at, last_error, key_version}`; 404 if not set
- [ ] `PUT /` body `{oanda_account_id, oanda_api_key, environment}` — upserts, clears `verified_at`, returns masked view (never echoes API key back)
- [ ] `DELETE /` — 204; also invalidates any running paper runs (they must halt if their credential rug-pulls)
- [ ] `POST /verify` — decrypts, calls OANDA `/v3/accounts/{id}/summary`, on 200 sets `verified_at=now()` and returns masked view with `verified: true`, on failure returns 400 with sanitised message

### `OandaClient` credential override

- [ ] `src/agentfx/adapters/oanda.py` — `OandaClient.__init__(api_key: str | None = None, account_id: str | None = None, environment: str = "PRACTICE")` — falls back to env vars only when both `api_key` and `account_id` are None. Paper runner ALWAYS passes explicit args from decrypted user credentials; shared candle ingestion continues to use env-var defaults.

### Tests

- [ ] `tests/unit/api/core/test_secrets.py` — Fernet round-trip, missing key fails fast, key-version routing
- [ ] `tests/integration/test_credentials.py` — full PUT/GET/DELETE/verify cycle against `test` profile; assert plaintext key never appears in any GET response or log line
- [ ] `tests/integration/test_credentials.py` — cross-user isolation: User A stores credentials, User B `GET /api/v1/users/me/credentials` returns own credentials or 404, never User A's
- [ ] Vitest for `CredentialsForm.tsx` in US-022

## API Contract

### Set credentials

```
PUT /api/v1/users/me/credentials
Content-Type: application/json
Authorization: Bearer <JWT>

{
  "oanda_account_id": "101-004-1234567-001",
  "oanda_api_key": "abcdef123456...",
  "environment": "PRACTICE"
}
```

Response 200:
```json
{
  "account_id_masked": "***7-001",
  "environment": "PRACTICE",
  "verified": false,
  "verified_at": null,
  "last_error": null,
  "key_version": 1
}
```

### Verify credentials

```
POST /api/v1/users/me/credentials/verify
Authorization: Bearer <JWT>
```

Response 200 (on success):
```json
{
  "account_id_masked": "***7-001",
  "environment": "PRACTICE",
  "verified": true,
  "verified_at": "2026-07-05T14:22:00Z",
  "last_error": null
}
```

Response 400 (on failure):
```json
{
  "detail": "OANDA verification failed: 401 Unauthorized"
}
```

### Get credentials

```
GET /api/v1/users/me/credentials
Authorization: Bearer <JWT>
```

Response 200 (masked, no plaintext key):
```json
{
  "account_id_masked": "***7-001",
  "environment": "PRACTICE",
  "verified": true,
  "verified_at": "2026-07-05T14:22:00Z",
  "last_error": null,
  "key_version": 1
}
```

Response 404: no credentials set for this user.

## Files Involved

- `src/agentfx/api/models/user.py` (edit) — add `UserOandaCredentials` SQLAlchemy model
- `src/agentfx/api/core/secrets.py` (new) — Fernet wrapper
- `src/agentfx/api/routers/user_credentials.py` (new)
- `src/agentfx/api/schemas/user_credentials.py` (new) — Pydantic request / response
- `src/agentfx/api/services/user_credentials.py` (new) — orchestration between router, encryption, OANDA verify call
- `src/agentfx/api/main.py` (edit) — register router; fail-fast on missing `FERNET_KEY`
- `src/agentfx/adapters/oanda.py` (edit) — accept credentials in constructor (see US-020 for order-execution methods)
- `migrations/versions/2026_07_XX_user_oanda_credentials.py` (new)
- `Dockerfile`, Railway config (edit) — inject `FERNET_KEY`
- `.env.development` (edit) — dev-only Fernet key + warning comment
- `tests/unit/api/core/test_secrets.py` (new)
- `tests/integration/test_credentials.py` (new)

## Verification

1. `make test-unit` — Fernet round-trip passes
2. `make test-integration` — credentials suite passes; cross-user isolation confirmed
3. Manual UAT: log in as TRADER, set OANDA Practice credentials, verify → verified badge appears with timestamp
4. Manual UAT: delete credentials → any running paper run for that user halts with `stop_reason=CREDENTIALS_REVOKED`
5. Grep source and logs: verify plaintext API key never appears in stdout, logs, or any API response body
