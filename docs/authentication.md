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

The first user to register at `POST /api/users/register` becomes
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
OIDC_REDIRECT_URI=https://akashic.example.com/api/auth/oidc/callback
```

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

- `GET /api/auth/providers` should return `{"oidc": true, ...}`.
- Hitting `/api/auth/oidc/login` should 302 to your IdP.
- After callback, decode the issued JWT — it should contain `sub`
  from the IdP and `auth_provider: "oidc"`.
- **Settings → Users** in the UI: the OIDC user should appear with
  resolved `FsBindings` linking them to source-aware identities.
  Each binding shows a confidence badge (`claim`, `ldap`, or
  `name`) so you can tell which strategy resolved it.

### Web UI status

`GET /api/auth/providers` is wired to the Login page, but the
"Sign in with OIDC" button is not yet rendered in the UI as of
the latest release. Until it ships, OIDC users sign in by hitting
`/api/auth/oidc/login` directly — typically by landing on a
corporate portal that links there, or by bookmarking it.

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

- `GET /api/auth/providers` returns `{"ldap": true, ...}`.
- `POST /api/auth/ldap/login` with `{"username": "...", "password": "..."}`
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
delivered as an HttpOnly cookie scoped to `/api/auth`. Replay of
a rotated refresh token revokes the entire chain — see
[permissions-model.md](permissions-model.md#refresh-tokens).
