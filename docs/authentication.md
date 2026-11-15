# Authentication

Akashic supports three authentication modes, configurable per
deployment. They can be combined — for example, OIDC for primary
sign-in plus the `ldap_fallback` strategy filling identity gaps
from AD.

## Modes at a glance

| Mode | When to use | Env flag | Identity-aware ACL filtering? |
|------|-------------|----------|--------------------------------|
| Local users | Default; small teams; demos | (always on) | No — viewer role only |
| OIDC | SSO via Keycloak / Authentik / Auth0 / etc. | `OIDC_ENABLED=true` | Yes — provisions `FsBindings` from claims |
| Direct LDAP | Username/password against AD without an IdP | `LDAP_ENABLED=true` | No — viewer role only |

OIDC is the recommended production mode because it's the only
path that resolves a user's group memberships into `FsBindings`,
which is what makes Search and Browse honour each user's actual
file access. Direct LDAP gives you AD password validation without
any identity-aware filtering — every LDAP user behaves like a
local user with respect to access control.

## Local users

The first user to register at `POST /api/v1/users/register` becomes
admin; registration closes after that.

To reset the admin password without losing data:

```sh
docker compose exec api python -c \
  "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('newpassword'))"
docker compose exec postgres psql -U akashic -d akashic \
  -c "UPDATE users SET password_hash = '<paste hash>' WHERE username = 'admin';"
```

Subsequent users are viewers; an admin promotes them via
**Settings → Users**.

## OIDC

### Required env vars

```sh
OIDC_ENABLED=true
OIDC_DISCOVERY_URL=https://idp.example.com/.well-known/openid-configuration
OIDC_CLIENT_ID=<from your IdP>
OIDC_CLIENT_SECRET=<from your IdP>
OIDC_REDIRECT_URI=https://akashic.example.com/api/v1/auth/oidc/callback
```

Akashic always sends a PKCE (S256) `code_challenge` and a `nonce` on the
authorization request and verifies both on the callback (the
`code_verifier` goes in the token request; the ID token's `nonce` must
match). Configure the IdP client as a confidential client with PKCE
allowed (or required). During the redirect, three short-lived HttpOnly
cookies carry the flow state: `oidc_state`, `oidc_pkce` and `oidc_nonce`.

Optional: `FRONTEND_URL` (default empty) is the SPA base URL used for
post-callback redirects. Leave empty when the SPA and API share an
origin; set it (e.g. `http://localhost:5173`) when they differ in dev.

### Claim mapping (defaults assume Authentik + AD federation)

```sh
OIDC_USERNAME_CLAIM=preferred_username
OIDC_EMAIL_CLAIM=email
OIDC_SID_CLAIM=onprem_sid          # AD objectSid
OIDC_UID_CLAIM=uidNumber           # POSIX UID for SSH/NFS sources
OIDC_GROUPS_CLAIM=groups
OIDC_GROUPS_FORMAT=sid             # one of: sid | name | dn
OIDC_DN_CLAIM=ldap_dn              # user's LDAP DN (for ldap_fallback)
```

If your IdP uses different claim names (Keycloak's group claim is
typically just `groups`; Auth0 uses namespaced custom claims), set
the matching `OIDC_*_CLAIM` overrides.

### Identity resolution strategy

```sh
OIDC_STRATEGY=auto    # one of: auto | claim | ldap_fallback | name_match
```

| Strategy | What it does | When to pick it |
|----------|--------------|-----------------|
| `auto` | Try `claim` → `ldap_fallback` → `name_match` and take the first that resolves anything. | Default. Adaptive but does the most work per login. |
| `claim` | Trust IdP claims only; never bind to LDAP. Fastest. | When the IdP can be configured to emit SID + UID + groups directly (Authentik with property mappers; Keycloak with realm-roles → group mappers). |
| `ldap_fallback` | Bind to AD as a service account at login to fill SID / UID / group gaps left by the IdP claims. | When you can't (or don't want to) configure your IdP to emit AD-shaped claims. |
| `name_match` | Last-resort: match group **names** as strings against POSIX/NFS source bindings. No SID resolution. | Non-AD environments where group names alone are meaningful. |

### Worked example (Authentik + AD)

See [oidc-authentik.md](oidc-authentik.md) for a step-by-step
setup including provider config, property mappers, and SID
extraction.

### Verifying it works

- `GET /api/v1/auth/providers` should return `{"oidc": true, ...}`.
- Hitting `/api/v1/auth/oidc/login` should 302 to your IdP.
- After the callback you should land on the dashboard; the browser
  holds an HttpOnly `akashic_refresh` cookie and `POST /api/v1/auth/refresh`
  returns an access token.
- **Settings → Users** in the UI: the OIDC user should appear with
  resolved `FsBindings` linking them to source-aware identities.
  Each binding shows a confidence badge (`claim`, `ldap`, or
  `name`) so you can tell which strategy resolved it.

### Web UI flow

When OIDC is enabled, the Login page shows a "Sign in with SSO" button.

1. The button navigates to `/api/v1/auth/oidc/login`, which redirects to the IdP.
2. The IdP returns to `/api/v1/auth/oidc/callback`, which sets the HttpOnly
   refresh cookie and 302s to the SPA's `/auth/callback`.
3. The SPA exchanges the refresh cookie for an access token via
   `POST /api/v1/auth/refresh` and navigates to the dashboard.

Failures redirect to `/login?error=<code>`:

- `sso_denied` — the IdP reported an error (e.g. user cancelled).
- `sso_state` — state mismatch/expired, or no authorization code.
- `sso_failed` — code exchange with the IdP failed.
- `sso_provision` — the user account could not be created.

## Direct LDAP login

For deployments where there is no OIDC IdP and you just want users
to log in with their AD credentials. **No identity resolution into
`FsBindings`** — every LDAP user gets the viewer role and sees
what the role allows, the same as a local user.

### Required env vars

```sh
LDAP_ENABLED=true
LDAP_SERVER=ldaps://ad.example.com:636
LDAP_BIND_DN=CN=akashic-svc,OU=Service Accounts,DC=example,DC=com
LDAP_BIND_PASSWORD=…
LDAP_USER_BASE=OU=Users,DC=example,DC=com
LDAP_USER_FILTER=(sAMAccountName={username})
```

The bind DN is a service account that does the user lookup. The
user's *own* password is then validated by binding as the user's
DN — Akashic never stores it.

### Verifying it works

- `GET /api/v1/auth/providers` returns `{"ldap": true, ...}`.
- `POST /api/v1/auth/ldap/login` with `{"username": "...", "password": "..."}`
  returns a JWT.

## OIDC + LDAP fallback (filling claim gaps)

This is **not a separate auth method** — it's an OIDC strategy
that binds to AD at login time to enrich whatever claims your IdP
emits. Use it when:

- Your IdP authenticates against AD but doesn't propagate
  `objectSid` / group SIDs into the OIDC claims, **or**
- You don't control the IdP's claim mappers.

### Configuration

Set the strategy and reuse the same `LDAP_*` vars as Direct LDAP:

```sh
OIDC_STRATEGY=ldap_fallback        # or 'auto' (auto includes ldap_fallback as the 2nd leg)
OIDC_DN_CLAIM=ldap_dn              # if your IdP can emit the user's full DN (preferred)
LDAP_SERVER=ldaps://ad.example.com:636
LDAP_BIND_DN=CN=akashic-svc,OU=Service Accounts,DC=example,DC=com
LDAP_BIND_PASSWORD=…
LDAP_USER_BASE=OU=Users,DC=example,DC=com
LDAP_USER_FILTER=(sAMAccountName={username})
```

### Flow

1. OIDC login completes; ID token validated against the IdP's
   JWKS.
2. Akashic checks for the SID claim (`OIDC_SID_CLAIM`).
3. If missing, binds to LDAP as the service account and looks
   the user up by — in priority order — DN (from `OIDC_DN_CLAIM`),
   email (`mail` / `userPrincipalName`), or username
   (`sAMAccountName`).
4. Retrieves `objectSid` + `memberOf`, then queries each group's
   `objectSid` so bindings store SIDs, not DNs.
5. The resolved identities are stored as `FsBindings` exactly as
   if the IdP had emitted them directly. Each carries
   `confidence="ldap"` so the UI badges them.

### Reliability

A 3-fail / 60-second circuit breaker
([`oidc_provisioning.py`](../api/akashic/auth/oidc_provisioning.py))
protects against AD outages: if the LDAP bind fails 3 times within
60 seconds, the breaker opens for 60 seconds and all further
fallback attempts short-circuit to "no result". Login still
succeeds — identity just falls back to whatever the OIDC claims do
contain. The breaker resets on the first success.

LDAP operations time out after 10 seconds per call (not currently
env-configurable).

## Group cache

Once identity is resolved, Akashic caches the per-user group →
file-visibility mapping for `GROUP_CACHE_TTL_HOURS` (default 24 —
see [configuration.md](configuration.md)). Bump this for
performance on large directory structures; lower it if group
membership changes need to take effect quickly.

## Refresh tokens

The OIDC and LDAP login flows both mint a short-lived access
token (`ACCESS_TOKEN_EXPIRE_MINUTES`, default 60) plus a
long-lived refresh token (`REFRESH_TOKEN_EXPIRE_DAYS`, default 30)
delivered as an HttpOnly cookie scoped to `/api/v1/auth`. Replay of
a rotated refresh token revokes the entire chain — see
[permissions-model.md](permissions-model.md#refresh-tokens).

## Scopes and API tokens

Every API route is authorised by a *scope*, never by role. A role is
a named bundle of scopes, and a request can do only what its
effective scopes allow. `GET /api/v1/scopes` is the authoritative
catalog (names, descriptions and the role bundles); the table below
is a snapshot. `GET /api/v1/users/me` returns the caller's effective
`scopes`.

| Scope | Allows | In viewer bundle |
|-------|--------|------------------|
| `files:read` | Search, browse and read file and directory metadata, including duplicates. | yes |
| `files:content` | Download or preview file contents (kept separate from reading metadata). | yes |
| `tags:read` | List tags and the tags applied to files. | yes |
| `tags:write` | Create and delete tags, and apply or remove them on files. | no |
| `analytics:read` | Read analytics, the dashboard and the storage explorer. | yes |
| `sources:read` | List sources and read their details, audit trail and reachability. | yes |
| `sources:write` | Create, test, update, delete and re-attach sources. | no |
| `credentials:list` | List credential profile names and ids (no secrets), e.g. to fill a source form. | yes |
| `credentials:read` | Read credential profiles, and OAuth credentials and providers. | no |
| `credentials:write` | Create, change and delete credential profiles and OAuth credentials and providers. | no |
| `scans:read` | Read scans, scan progress and logs, and subscribe to scan streams. | yes |
| `scans:write` | Trigger and cancel scans. | no |
| `scanners:read` | Read scanners, claim tokens, discovery requests and scanner reachability. | no |
| `scanners:write` | Register, change and remove scanners and claim tokens; approve discoveries. | no |
| `hosts:read` | Read hosts and their scanner reachability. | yes |
| `hosts:write` | Create, change and remove hosts, and test or add their shares. | no |
| `identities:read` | Read identities, bindings, principals, effective permissions and access reports. | yes |
| `access:read` | Run access reports: who can access what, across every principal's ACLs. | no |
| `identities:write` | Manage identities and bindings, and resolve their groups. | yes |
| `webhooks:read` | List webhooks. | yes |
| `webhooks:write` | Create and delete webhooks. | yes |
| `users:read` | Read other users' accounts and unbound identities. | no |
| `users:write` | Create users, and see files regardless of per-user ACL filtering. | no |
| `settings:read` | Read server settings. | no |
| `settings:write` | Change server settings. | no |
| `audit:read` | Read the admin audit log. | no |
| `health:read` | Read dependency health and service activity. | no |
| `maintenance:write` | Run maintenance jobs. | no |
| `data:purge` | Permanently purge indexed data. Destructive, so it must be granted explicitly. | no |
| `tokens:read` | List your own API tokens. | yes |
| `tokens:write` | Create, change and revoke your own API tokens. | yes |
| `tokens:admin` | List and revoke every user's API tokens. | no |

### Role bundles

- **viewer** — exactly what a viewer could do before scopes existed:
  read the index and its own data, manage its own identities,
  webhooks and API tokens (the "yes" column above).
- **admin** — every scope, including `data:purge`, `users:write`
  (which also sees files regardless of per-user ACL filtering) and
  `tokens:admin`.
- An unknown role grants nothing.

### Creating a token

In the web UI: **Settings → API tokens → New token**. Pick a name,
the scopes (only scopes you hold are offered; "Read-only" and "Full"
are shortcuts), optionally limit it to some sources, and choose an
expiry. The token (`akp_...`) is shown once; copy it then. Only its
hash is stored. Admins also get an "All tokens" view where they can
revoke anyone's token.

With curl (from a login session; see the interactive-session rule
below):

```bash
curl -sX POST https://akashic.example.com/api/v1/me/tokens \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"name": "nightly export", "scopes": ["files:read", "sources:read"],
       "source_ids": null, "expires_at": "2027-01-01T00:00:00Z"}'
# -> 201 {"id": "...", "token": "akp_...", ...}   (the token is returned once)
```

`source_ids` and `expires_at` are optional. Manage tokens with
`GET /api/v1/me/tokens`, `PATCH /api/v1/me/tokens/{id}` (rename or
narrow scopes, never widen) and `DELETE /api/v1/me/tokens/{id}`
(revoke). Admins: `GET`/`DELETE /api/v1/admin/tokens` (`tokens:admin`).

### Using a token

```bash
curl -H "Authorization: Bearer akp_..." https://akashic.example.com/api/v1/search?q=invoice
```

Tokens work on the REST API only. They are not accepted for
WebSockets, scanner endpoints or the ingest audience.

### Effective scopes: token ∩ current role

A token's effective scopes are the scopes stored on the token
intersected with its owner's **current** role bundle, evaluated on
every request. Demoting a user shrinks their tokens immediately.
Revoked, expired, unknown or deleted-owner tokens get 401. A token
can never hold more than its creator held when it was created (422
otherwise).

### Interactive-session rule

Creating a token and PATCHing one require a login session (a JWT), not
another API token; a token calling them gets 403
`interactive_session_required`. Otherwise a token could mint
non-expiring tokens that outlive its own revocation. Listing and
revoking your own tokens with a token still works.

### Source limits

A token created with `source_ids` is limited to those sources:

- Anything outside the limit answers **404**, never 403, across
  search, browse, entries, content, duplicates, analytics, dashboard,
  storage, access, scans, sources and tags. Search results are
  filtered to the allowed sources.
- Routes that are not source-aware (hosts, credentials, scanners,
  identities, webhooks, users, settings, audit, health, maintenance,
  purge, tokens) and actions reaching beyond a source (creating
  sources, the tag catalogue, scanner allow-lists) answer **403**
  `source_limited`.
- A limited token may hold every read-only scope plus the write scopes
  `sources:write`, `scans:write` and `tags:write`. Every other write
  scope is refused with 422 at creation: `credentials:write`,
  `hosts:write`, `identities:write`, `scanners:write`,
  `settings:write`, `users:write`, `webhooks:write`,
  `maintenance:write`, `tokens:write`, plus `data:purge` and
  `tokens:admin`. The authoritative per-scope answer is the
  `allowed_with_source_limit` flag on each `GET /api/v1/scopes` entry;
  the same response's `read_only` flag marks the scopes the web UI's
  "Read-only" preset selects (every `:read` scope plus `files:content`
  and `credentials:list`).
- On `PATCH /sources/{id}` it may change only `name`,
  `scan_schedule`, `exclude_patterns`, `preferred_pool`,
  `max_parallel_scanners`, `scan_chunk_size` and `is_removable`;
  connection config, credential profile and host give 403
  `source_limited`.
- It can only apply tags that already exist; applying an unknown tag
  name gives 403 `source_limited`. `GET /tags` lists only tags used
  in its sources.
- A limited token never bypasses filesystem ACL filtering: it holds
  no `users:write`, so the owner's per-user ACL filtering always
  applies.
- Known limitation: because applying an unknown tag is refused, a
  limited token can tell whether a tag name exists in the catalogue.

### Error codes

Errors are `{code, message, detail?, request_id}`.

| Status | `code` | Meaning |
|--------|--------|---------|
| 401 | `unauthorized` | Missing, invalid, expired or revoked credential. |
| 403 | `insufficient_scope` | The credential lacks the route's scope (the message names it). |
| 403 | `source_limited` | A source-limited token used a route or action outside its sources. |
| 403 | `interactive_session_required` | Creating or changing a token needs a login session. |
| 404 | `not_found` | Missing, or outside a token's source limit. |
| 422 | `validation_error` | E.g. scopes beyond your own, unknown scopes or sources, write scopes on a limited token, past expiry. |
