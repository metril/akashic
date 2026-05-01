# Setting up OIDC + AD permission filtering with Authentik

Akashic supports OIDC login plus permission-aware indexing — when configured correctly, a logged-in user only sees files whose ACLs grant them access. This guide walks through the full Authentik + Active Directory setup that makes that happen.

The goal: log in via Authentik with your AD account → Akashic auto-creates `FsPerson` and `FsBinding` records derived from your AD SID and group SIDs → searches and Browse pages filter to files where your principal set has `read` rights.

## Architecture

```
+----------+   AD federation   +-----------+   OIDC code-flow   +---------+
|  AD DC   | <---------------> | Authentik | <----------------- | Akashic |
+----------+                   +-----------+                    +---------+
   ^                                  |                              |
   | LSARPC (SID resolution)          | ID token with                |
   | from akashic-scanner             | objectSid + group claims     |
   |                                  v                              |
   +-------------------- ACLs in scanned files <----------------+
                          (already indexed)
```

Three things happen at login:

1. The user clicks "Sign in with OIDC" → Authentik authenticates them against your AD via its LDAP federation.
2. Authentik issues an OIDC ID token containing the user's `objectSid` claim and a `groups` claim (group SIDs).
3. Akashic decodes the token, extracts SIDs, and calls `sync_fs_bindings_from_claims` which upserts `FsBinding` rows for every Akashic source whose `connection_config.principal_domain` matches the SID's domain prefix.

From then on, every Search and Browse query intersects the user's SID set against the indexed `viewable_by_read` field — the user only sees what their AD account would see if they walked the share by hand.

## Prerequisites

- An Authentik instance reachable from the Akashic api host (typically running in the same docker-compose stack or behind a reverse proxy).
- An AD domain controller reachable from Authentik. A read-only service account in AD is sufficient; Authentik never needs to write to AD.
- Akashic 0.1.x or later (Phase 2a OIDC plumbing).

## Step 1 — Federate AD into Authentik

In Authentik admin → **Directory → Federation & Social login → Create LDAP Source**:

- **Server URI**: `ldaps://dc.example.com:636` (use LDAPS in production; STARTTLS works too).
- **Bind credentials**: a service account, e.g. `CN=akashic-svc,OU=Service Accounts,DC=example,DC=com`.
- **Base DN**: the domain root, e.g. `DC=example,DC=com`.
- **User search filter**: `(&(objectClass=user)(!(objectClass=computer)))`.
- **Object uniqueness field**: `objectSid` (so Authentik recognizes the same AD user across re-syncs).

Click "Sync now" and verify users are imported.

## Step 2 — Map `objectSid` into a custom user attribute

By default Authentik does not expose `objectSid` to OIDC tokens. We map it via a property mapper:

In **Customisation → Property Mappings → Create LDAP Property Mapping**:

- **Name**: `AD objectSid → onprem_sid`
- **Object field**: `attributes.onprem_sid`
- **Expression**:
  ```python
  # Authentik's expression policy receives the LDAP entry as `ldap`.
  # objectSid arrives as a binary blob; convert to the canonical
  # S-1-5-21-... string form that Akashic's ACL data uses.
  import ldap3.utils.conv as _conv
  raw = ldap.get("objectSid", [None])[0]
  return _conv.escape_filter_chars(raw, encode_for="STRING") if raw else None
  ```

  Authentik supports a built-in helper for this — if your version exposes `to_sid_string()` use that; otherwise the snippet above does the same thing.

Attach the mapper to your LDAP source under **User Property Mappings**.

Re-sync the source. Spot-check a user in **Directory → Users → (pick one) → Attributes** — they should have `onprem_sid` set to a string like `S-1-5-21-1234567890-987654321-1001`.

## Step 3 — Map AD group SIDs into the `groups` claim

Same idea, different mapping:

- **Name**: `AD group SIDs → groups`
- **Object field**: `groups` (Authentik recognizes this as the OIDC `groups` claim source).
- **Expression**:
  ```python
  # ldap['memberOf'] is a list of group DNs. We need group SIDs, not DNs.
  # The cleanest path is to have Authentik's group sync also populate
  # objectSid on each group, then return a list of those.
  return [g.attributes.get("onprem_sid") for g in user.ak_groups.all() if g.attributes.get("onprem_sid")]
  ```

You'll need a parallel mapper on the **Group Property Mappings** that pulls `objectSid` onto each Authentik group during LDAP group sync (same expression as the user one but reading from the group's own LDAP entry).

After this, an Authentik user object has `attributes.onprem_sid = "S-1-5-..."` and `groups = ["S-1-5-21-...-1001", "S-1-5-21-...-1002", ...]`.

## Step 4 — Create an OAuth2/OpenID Provider in Authentik

In **Applications → Providers → Create → OAuth2/OpenID Provider**:

- **Name**: `Akashic`.
- **Client type**: Confidential.
- **Client ID / Client secret**: Authentik generates these — copy them into your Akashic `.env` (see Step 6).
- **Redirect URIs**: `http://akashic.local:8000/api/auth/oidc/callback` (replace with your Akashic external URL).
- **Signing key**: any RS256 key Authentik provides.
- **Scopes**: `openid email profile groups`.
- **Subject mode**: based on the user's email or username (either works; Akashic stores it as `external_id`).

Under **Advanced flow settings → Property mappings**:

- Add a custom **Scope Mapping** named `claim:onprem_sid` that emits the `onprem_sid` claim from `user.attributes`:
  ```python
  return {"onprem_sid": user.attributes.get("onprem_sid")}
  ```
- Make sure the default `groups` mapping is enabled.

## Step 5 — Wire the provider to an Application

In **Applications → Applications → Create**:

- **Name**: `Akashic`.
- **Slug**: `akashic`.
- **Provider**: select the provider you created in Step 4.
- **Launch URL**: your Akashic web URL.

## Step 6 — Configure Akashic

In Akashic's `.env` (or compose env block):

```ini
OIDC_ENABLED=true
OIDC_DISCOVERY_URL=https://authentik.example.com/application/o/akashic/.well-known/openid-configuration
OIDC_CLIENT_ID=<from Step 4>
OIDC_CLIENT_SECRET=<from Step 4>
OIDC_REDIRECT_URI=http://akashic.local:8000/api/auth/oidc/callback

# Strategy: "auto" tries claim → ldap_fallback → name_match in order.
# Pin to a single strategy if you want predictable behaviour.
OIDC_STRATEGY=auto

# Claim names — Authentik with the mappers above emits these:
OIDC_USERNAME_CLAIM=preferred_username
OIDC_EMAIL_CLAIM=email
OIDC_SID_CLAIM=onprem_sid
OIDC_GROUPS_CLAIM=groups
OIDC_GROUPS_FORMAT=sid
```

Restart the api: `docker compose restart api`.

## Step 7 — Mark each source's principal domain

For each Akashic source whose ACLs use AD SIDs (typically SMB shares), add a `principal_domain` to its `connection_config`:

```sql
UPDATE sources
SET connection_config = connection_config || '{"principal_domain":"S-1-5-21-1234567890-987654321"}'
WHERE name = 'finance-share';
```

The value is the SID prefix that identifies your AD domain — everything before the final RID (`-1001`, `-1002`, etc.). When the user logs in with `onprem_sid = S-1-5-21-1234567890-987654321-1042`, Akashic matches the prefix and creates an `FsBinding` for that source. SIDs whose domain doesn't match any source land in `fs_unbound_identities` (see "Verifying" below).

## Verifying it works

1. Log out, log in via OIDC. Akashic should redirect to Authentik, then back, and land you on the dashboard.
2. **Settings → Identities** shows one or more bindings. Each carries a "Login source" badge: `claim` (best — SID came from the OIDC token), `ldap` (we hit AD over LDAP), or `name` (string-name fallback only).
3. Run a Search with `q=*` and an extension filter — you should see only files your AD account has read access to. Compare with what the dev `admin` user (no FsBindings) sees as a sanity check; admins see everything.
4. Check `fs_unbound_identities` for any SIDs Akashic could not match to a source. Typical cause: a `principal_domain` is missing on a source whose ACLs use that SID's domain.

## Strategy fallbacks

If your IdP can't emit SIDs (some Keycloak setups, custom OIDC providers), set `OIDC_STRATEGY=ldap_fallback`. Akashic will skip the SID claim and instead bind to the AD over LDAP at login, fetching `objectSid` and group memberships directly.

For non-AD environments where neither claims nor LDAP are available, `OIDC_STRATEGY=name_match` falls back to matching on owner_name / group_name strings as they appear in the scanned ACLs. This is weaker — it won't match raw NT SIDs in scanned ACL data — but works for POSIX-only deployments.

## Common pitfalls

- **objectSid is base64-encoded and Akashic doesn't recognize it.** Either (a) update your Authentik mapper to convert to canonical S-1-5-... string form before emitting, or (b) accept the base64 form — Akashic's `extract_identities` handles both.
- **Token has groups but no onprem_sid.** Akashic falls through to the `groups` claim; with `OIDC_GROUPS_FORMAT=sid` those are matched directly against ACL group SIDs. With `OIDC_GROUPS_FORMAT=name`, they're matched against `owner_name` strings in indexed entries — which works for POSIX/NFS but rarely for SMB.
- **No bindings created at login.** Check `fs_unbound_identities` for the user. If every claim landed there, no source has a matching `principal_domain` — go back to Step 7.
- **Stale group caches.** TTL is 24h. Use the "Refresh groups" button in Settings → Identities to force a re-fetch after a group-membership change.

## What the code does

The relevant entry points, for code review or debugging:

- [api/akashic/auth/oidc.py](../api/akashic/auth/oidc.py) — discovery, JWKS, code-flow, calls `sync_fs_bindings_from_claims` after the user record is created/updated.
- [api/akashic/auth/oidc_provisioning.py](../api/akashic/auth/oidc_provisioning.py) — `extract_identities(claims, settings)` and the per-strategy branching.
- [api/akashic/services/group_resolver.py](../api/akashic/services/group_resolver.py) — reused for the `ldap_fallback` strategy.
- [api/akashic/services/acl_denorm.py](../api/akashic/services/acl_denorm.py) — the denormalization that turns ACL data into the `viewable_by_read/write/delete` token sets at scan time.
- [api/akashic/routers/search.py](../api/akashic/routers/search.py) and [api/akashic/routers/browse.py](../api/akashic/routers/browse.py) — query-time intersection of user tokens against indexed tokens.
