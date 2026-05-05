# Browser Extension API Contract

This document describes the server-side API surface that the Client St0r
browser extension binary calls. The extension itself (Chrome / Firefox /
Edge `.crx` / `.xpi` package, store submission, content-script injection)
lives in a separate codebase. **All endpoints documented here are
server-side scaffolding shipped under Phase 28.**

Phase 28 was shipped server-side across v3.17.327 through v3.17.331.

---

## Authentication

The extension uses **bearer-token authentication**. It does NOT carry the
user's Django session cookie because it lives in a different origin from
the app.

### Token issuance — session-authed

The user's first action is to log into the app (`https://your-instance/`)
and click "Issue extension token" in Settings. That triggers:

```
POST /vault/api/extension/tokens/issue/
```

Body (form-encoded or JSON):

| Field             | Type    | Required | Description                                        |
|-------------------|---------|----------|----------------------------------------------------|
| `label`           | string  | no       | "Chrome on Mac" — UX hint to identify the install. |
| `organization_id` | int     | no       | Pin the token to a single org.                     |
| `ttl_days`        | int     | no       | TTL override; default 30, capped at 365.           |

Returns 201:

```json
{
  "id": 42,
  "token": "kS7g…snip…GpA",
  "label": "Chrome on Mac",
  "organization_id": null,
  "expires_at": "2026-06-04T20:59:00+00:00",
  "created_at": "2026-05-05T20:59:00+00:00"
}
```

The `token` field is returned **exactly once**. The user copies it into
the extension's options page; the server never surfaces it again.

Audit-logs as `create` on `vault.WebExtensionAuthToken`.

### Token list

```
GET /vault/api/extension/tokens/
```

Returns the calling user's tokens (no secret material):

```json
{
  "tokens": [
    {
      "id": 42, "label": "Chrome on Mac",
      "organization_id": null,
      "created_at": "...", "last_used_at": "...", "expires_at": "...",
      "revoked_at": null, "is_active": true
    }
  ]
}
```

### Token revocation

```
DELETE /vault/api/extension/tokens/<pk>/revoke/
```

(also accepts POST for form-fallback flows). Marks `revoked_at`. Owner
or superuser only — 403 otherwise. Audit-logs as `delete`.

### Bearer header

Every other endpoint below requires:

```
Authorization: Bearer <token>
```

The `extension_auth_required` decorator:

* validates the token (404 if not found, 401 if revoked / expired)
* attaches `request.user` and `request.extension_token`
* resolves `request.current_organization` from:
  1. `X-Organization-Id` header (if set, must be an active org)
  2. token's pinned `organization` (if set)
  3. None (global view — only superusers / staff can see anything)
* bumps `last_used_at`

---

## Organization context

Extensions that operate across multiple orgs send a per-call header:

```
X-Organization-Id: 7
```

Tokens pinned to a single org ignore the header — they always resolve to
the pinned org. Tokens NOT pinned honor the header per call. If neither
the header nor the pin is set, the request is in "global view" and only
returns rows the user can see across all orgs (superusers / staff users).

---

## Endpoint catalogue

| Endpoint                                              | Method | Auth   | RoleTemplate perm              | Audit log action            | Shipped     |
|-------------------------------------------------------|--------|--------|--------------------------------|-----------------------------|-------------|
| `/vault/api/extension/tokens/`                        | GET    | session| (any logged-in user)           | —                           | v3.17.327   |
| `/vault/api/extension/tokens/issue/`                  | POST   | session| (any logged-in user)           | `create`                    | v3.17.327   |
| `/vault/api/extension/tokens/<pk>/revoke/`            | DELETE | session| (any logged-in user)           | `delete`                    | v3.17.327   |
| `/vault/api/extension/autofill/?url=…`                | GET    | bearer | `vault_extension_use`          | `vault_autofill`            | v3.17.328   |
| `/vault/api/extension/sync/?cursor=&limit=`           | GET    | bearer | `vault_extension_offline_cache`| `vault_extension_sync`      | v3.17.328   |
| `/vault/api/extension/<pk>/totp/`                     | GET    | bearer | `vault_extension_use`          | `vault_extension_totp`      | v3.17.329   |
| `/vault/api/extension/<pk>/reveal/`                   | POST   | bearer | `vault_extension_use`          | `vault_extension_reveal`    | v3.17.329   |
| `/vault/api/extension/verify-master/nonce/`           | GET    | bearer | `vault_extension_use`          | —                           | v3.17.329   |
| `/vault/api/extension/verify-master/`                 | POST   | bearer | `vault_extension_use`          | `vault_extension_verify_master` | v3.17.329 |
| `/vault/api/extension/generate/?length=&symbols=&…`   | GET    | bearer | `vault_extension_use`          | —                           | v3.17.330   |

Audit log rows are emitted with `extra_data.event = "<action>"` so a
search-friendly key is always present.

---

## Endpoint details

### `GET /vault/api/extension/autofill/`

Match credentials by URL.

Query params:

| Param | Required | Description                                |
|-------|----------|--------------------------------------------|
| `url` | yes      | The page URL the extension wants to autofill on. |

Match logic: parse the host (lowercase, sans port) and match against
`Password.url`'s host. Match passes when:

* hosts are equal, OR
* the target host is a subdomain of a stored password's host
  (`login.example.com` matches a credential stored for `example.com`), OR
* a stored password's host is a subdomain of the target host (rare —
  user typed the bare domain into the page).

Capped at 50 returned matches / 500 inspected rows. Personal-vault entries
(`is_personal=True`) are excluded.

Response:

```json
{
  "host": "example.com",
  "count": 1,
  "matches": [
    {
      "id": 17,
      "title": "Example admin panel",
      "username": "alice@example.com",
      "totp_available": true,
      "url": "https://example.com/admin"
    }
  ]
}
```

Errors:

* `400` — `url` parameter missing or unparseable.
* `403` — RoleTemplate is missing `vault_extension_use`.

### `GET /vault/api/extension/sync/`

Bulk-sync visible passwords as **encrypted blobs** for the offline cache.

Query params:

| Param    | Default | Description                                        |
|----------|---------|----------------------------------------------------|
| `cursor` | `null`  | Last `id` from the previous page; cursor-paginated.|
| `limit`  | `100`   | Page size (1 ≤ limit ≤ 500).                       |

Response:

```json
{
  "count": 100,
  "next_cursor": 12345,
  "has_more": true,
  "passwords": [
    {
      "id": 17,
      "title": "Example admin panel",
      "username": "alice@example.com",
      "url": "https://example.com/admin",
      "organization_id": 7,
      "encrypted_password": "<base64 blob — decrypt client-side>",
      "password_type": "website",
      "totp_available": true,
      "updated_at": "2026-05-05T20:00:00+00:00"
    }
  ]
}
```

The server **never** decrypts on this endpoint. The extension decrypts
client-side using the master-derived key.

Errors:

* `400` — invalid `cursor` value.
* `403` — RoleTemplate is missing `vault_extension_offline_cache`.

### `GET /vault/api/extension/<pk>/totp/`

Returns the current TOTP code.

Response:

```json
{
  "code": "847291",
  "time_remaining": 17,
  "valid_until_unix": 1717538237,
  "issuer": "Example admin panel"
}
```

Errors:

* `400` — password has no TOTP secret configured (or the stored secret is malformed).
* `403` — missing `vault_extension_use`.
* `404` — password not visible to caller in current org.

### `POST /vault/api/extension/<pk>/reveal/`

Returns plaintext for autofill.

Body: empty (the bearer token IS the auth).

Response:

```json
{ "password": "hunter2" }
```

When `Password.requires_reveal_approval=True` and no valid approval exists:

```json
HTTP/1.1 403 Forbidden
{ "error": "Reveal approval required.", "requires_approval": true }
```

A satisfying approval is marked as used (single-use) on a successful
reveal. Audit-logs success and denial separately.

Errors:

* `403` — missing `vault_extension_use` OR `requires_approval=true`.
* `404` — password not visible to caller in current org.
* `500` — decrypt failure (logged).

### Master-password verify (proof-of-knowledge)

This is intentionally a minimal stub — drop-in-replaceable to a stronger
KDF (PBKDF2 / Argon2) without changing the API shape.

#### Step 1: get a nonce

```
GET /vault/api/extension/verify-master/nonce/
```

Returns:

```json
{ "nonce": "v1b…snip…", "ttl_seconds": 60 }
```

The nonce is cached server-side keyed by the bearer token's id.

#### Step 2: prove knowledge

The extension computes `HMAC_SHA256(derived_key, nonce_bytes)` where
`derived_key` is derived from the user-typed master password using the
KDF described in the extension docs. The current server-side stub
expects:

```
derived_key = SHA256(user.password)  # the Django password hash
```

(The extension reproduces this by being told the user's password hash
length / salt — for production-grade impl, replace this with
PBKDF2/Argon2 over a stored-on-server user salt.)

Then:

```
POST /vault/api/extension/verify-master/
Content-Type: application/json

{ "nonce": "v1b…snip…", "hmac_hex": "<lowercase hex digest>" }
```

Response on match:

```json
{ "verified": true }
```

Response on mismatch / nonce expired:

```json
HTTP/1.1 401 Unauthorized
{ "error": "HMAC mismatch." }
```

The nonce is single-use — it's burned on POST regardless of outcome.
Audit-logs `vault_extension_verify_master` with `verified: true|false`.

### `GET /vault/api/extension/generate/`

Strong-password generator for the extension's "fill new credential" UI.

Query params:

| Param       | Default | Description                                |
|-------------|---------|--------------------------------------------|
| `length`    | 24      | Clamped 8 ≤ length ≤ 128.                  |
| `uppercase` | 1       | Include uppercase letters.                 |
| `lowercase` | 1       | Include lowercase letters.                 |
| `numbers`   | 1       | Include digits (`digits=` also accepted).  |
| `symbols`   | 1       | Include `!@#$%^&*()_+-=[]{}|;:,.<>?`.      |

Boolean params accept `1/0`, `true/false`, `yes/no`, `on/off`.

Response:

```json
{
  "password": "kp9$Wq…",
  "length": 24,
  "charset_size": 88,
  "entropy_bits": 154.96
}
```

Errors:

* `400` — `length` not a valid int, OR no character class selected.
* `403` — missing `vault_extension_use`.

---

## RoleTemplate permission flags

Two new boolean fields on `accounts.RoleTemplate` (added in v3.17.328,
migration `accounts/0032_*`):

| Field                            | Default | Required for                                              |
|----------------------------------|---------|-----------------------------------------------------------|
| `vault_extension_use`            | False   | All bearer-authed extension endpoints except sync.        |
| `vault_extension_offline_cache`  | False   | The bulk-sync endpoint that returns encrypted blobs.      |

Simple-role fallback (when `Membership.role_template` is null):

| Role      | `_use` | `_offline_cache` |
|-----------|--------|------------------|
| Owner     | True   | True             |
| Admin     | True   | True             |
| Editor    | True   | False            |
| Read-Only | False  | False            |

Superusers and `is_staff_user()` users always pass — the gate only
applies to ordinary org members.

---

## Auditing

Every bearer-authed extension call writes an `audit.AuditLog` row with:

* `user` — the token's owner
* `organization` — resolved per the X-Organization-Id / pinned-org rules
* `object_type` — `vault.Password` for per-credential calls, `vault.WebExtensionAuthToken` for token-lifecycle calls, `vault.Password` (with `object_id=None`) for autofill / sync (which are queries, not single-row reads)
* `extra_data.event` — string-keyed event name for fast filtering: `vault_autofill`, `vault_extension_sync`, `vault_extension_totp`, `vault_extension_reveal`, `vault_extension_verify_master`
* `success` — False on permission denials / decrypt failures / verify-master mismatches

To list every extension action a user took:

```python
from audit.models import AuditLog
AuditLog.objects.filter(
    user=user,
    extra_data__event__startswith='vault_',
).order_by('-timestamp')
```

---

## Versioning + change policy

This contract is **stable** as of v3.17.331. Future changes will be
additive (new optional query params, new optional response fields).
Breaking changes will require a new versioned URL prefix
(`/vault/api/extension/v2/...`).

The extension binary should send `User-Agent: ClientStor-Extension/<version>`
so the server can distinguish extension calls from other API clients in
audit logs.
