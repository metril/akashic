<!-- Generated from openapi.json by api/scripts/render_api_md.py. Do not edit; run `make openapi`. -->
# Akashic API reference

Version 0.51.0. Generated from [`openapi.json`](../openapi.json); the live, interactive version is served at `/api/docs`.

Universal file index: sources, scans, search, permissions and administration.

## Conventions

- HTTP endpoints live under `/api/v1`, WebSockets under `/ws/v1`. Unversioned `/api/*` answers 410 `api_version_required`.
- Send `Authorization: Bearer <token>`: a login access token or a personal API token (`akp_...`).
- Lists return `{items, next_cursor, limit, total?}`; pass `next_cursor` back as `cursor`. `null` means the last page.
- Every non-2xx response is an `ErrorResponse` (below) and carries an `X-Request-ID` header.

## Authentication

| Scheme | Type | Description |
| --- | --- | --- |
| `apiToken` | http/bearer | Long-lived personal API token, sent as `Authorization: Bearer <token>`. |
| `ingestJWT` | http/bearer | Ingest-scoped token leased to a scanner for one scan. Internal. |
| `scannerJWT` | http/bearer | Per-request JWT signed with the scanner's Ed25519 key. Internal. |
| `userJWT` | http/bearer | Short-lived access token from `POST /api/v1/users/login` or `/api/v1/auth/refresh`. Each operation lists the scope it requires; see `GET /api/v1/scopes`. |

### Scopes

| Scope | Grants |
| --- | --- |
| `access:read` | Run access reports: who can access what, across every principal's ACLs. |
| `analytics:read` | Read analytics, the dashboard and the storage explorer. |
| `audit:read` | Read the admin audit log. |
| `credentials:list` | List credential profile names and ids (no secrets), e.g. to fill a source form. |
| `credentials:read` | Read credential profiles, and OAuth credentials and providers. |
| `credentials:write` | Create, change and delete credential profiles and OAuth credentials and providers. |
| `data:purge` | Permanently purge indexed data. Destructive, so it must be granted explicitly. |
| `files:content` | Download or preview file contents (kept separate from reading metadata). |
| `files:read` | Search, browse and read file and directory metadata, including duplicates. |
| `health:read` | Read dependency health and service activity. |
| `hosts:read` | Read hosts and their scanner reachability. |
| `hosts:write` | Create, change and remove hosts, and test or add their shares. |
| `identities:read` | Read identities, bindings, principals, effective permissions and access reports. |
| `identities:write` | Manage identities and bindings, and resolve their groups. |
| `maintenance:write` | Run maintenance jobs. |
| `scanners:read` | Read scanners, claim tokens, discovery requests and scanner reachability. |
| `scanners:write` | Register, change and remove scanners and claim tokens; approve discoveries. |
| `scans:read` | Read scans, scan progress and logs, and subscribe to scan streams. |
| `scans:write` | Trigger and cancel scans. |
| `settings:read` | Read server settings. |
| `settings:write` | Change server settings. |
| `sources:read` | List sources and read their details, audit trail and reachability. |
| `sources:write` | Create, test, update, delete and re-attach sources. |
| `tags:read` | List tags and the tags applied to files. |
| `tags:write` | Create and delete tags, and apply or remove them on files. |
| `tokens:admin` | List and revoke every user's API tokens. |
| `tokens:read` | List your own API tokens. |
| `tokens:write` | Create, change and revoke your own API tokens. |
| `users:read` | Read other users' accounts and unbound identities. |
| `users:write` | Create users, and see files regardless of per-user ACL filtering. |
| `webhooks:read` | List webhooks. |
| `webhooks:write` | Create and delete webhooks. |

## Errors

Body of every non-2xx response:
### ErrorResponse

Body of every non-2xx response.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `code` | `string` | yes | Stable machine-readable error code, e.g. `not_found` or `validation_error`. |
| `detail` | any \| `null` | yes | Optional structured extra. For `validation_error` this is the list of field errors (`loc`, `msg`, `type`); other errors may carry an object. |
| `message` | `string` | yes | Human-readable summary of what went wrong. |
| `request_id` | `string` | yes | Correlation id, also returned in the `X-Request-ID` header. |

### Error codes

| Status | `code` | Meaning |
| --- | --- | --- |
| 400 | `bad_request` | Malformed or unacceptable request. |
| 401 | `unauthorized` | Missing, invalid, expired or revoked credentials. |
| 403 | `forbidden` | Authenticated but not permitted. |
| 403 | `insufficient_scope` | The credential lacks the scope the operation requires. |
| 403 | `interactive_session_required` | Creating or changing API tokens needs a login session, not another token. |
| 403 | `source_limited` | The token is limited to specific sources and cannot do this. |
| 404 | `not_found` | Resource not found, or outside a source-limited token's sources. |
| 405 | `method_not_allowed` | The path does not support this method. |
| 409 | `conflict` | Conflicts with the current resource state. |
| 410 | `api_version_required` | Unversioned `/api/*` path; use `/api/v1/...`. |
| 413 | `payload_too_large` | Request body too large. |
| 422 | `validation_error` | Invalid input; `detail` lists the field errors. |
| 426 | `upgrade_required` | Scanner protocol version no longer accepted. |
| 429 | `rate_limited` | Too many requests. |
| 500 | `internal_error` | Unexpected server error. |
| 502 | `bad_gateway` | An upstream service returned an invalid response. |
| 503 | `service_unavailable` | A dependency is unavailable. |

## Endpoints

### auth

Login (local, LDAP and OIDC single sign-on), token refresh, logout, and the scope catalog.

- `POST /api/v1/auth/ldap/login`: Log in with LDAP
- `POST /api/v1/auth/logout`: Log out
- `GET /api/v1/auth/oidc/callback`: Finish OIDC login
- `GET /api/v1/auth/oidc/login`: Start OIDC login
- `GET /api/v1/auth/providers`: List login providers
- `POST /api/v1/auth/refresh`: Refresh the access token
- `GET /api/v1/scopes`: List scopes and role bundles

<a id="op-ldap-login"></a>
#### `POST /api/v1/auth/ldap/login`

**Log in with LDAP** (`ldap_login`)

Auth: None (public)

Public. Authenticates by LDAP bind, provisions the user on first login, returns an access token and sets the HttpOnly refresh cookie. Records an `ldap_login_success` audit event. Rate limited to 10 attempts per minute per client and username.
Errors: 401 for invalid credentials; 404 if LDAP is not enabled; 429 when rate limited; 500 if the directory fails or the user cannot be provisioned.

**Request body** (required)
- `application/json`: [`LDAPLoginRequest`](#schema-ldaploginrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`TokenResponse`](#schema-tokenresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 429 | Too many requests. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 500 | Internal Server Error | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-logout"></a>
#### `POST /api/v1/auth/logout`

**Log out** (`logout`)

Auth: None (public)

Public. Revokes the presented refresh token and clears the cookie. The short-lived access token keeps working until it expires. Idempotent: calling without a cookie succeeds.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `akashic_refresh` | cookie | `string` \| `null` | no | HttpOnly refresh cookie set at login. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`LogoutResponse`](#schema-logoutresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-oidc-callback"></a>
#### `GET /api/v1/auth/oidc/callback`

**Finish OIDC login** (`oidc_callback`)

Auth: None (public)

Public. Exchanges the authorization code for tokens, provisions the user if needed, sets the HttpOnly refresh cookie and redirects (302) to the SPA's `/auth/callback`, which then calls `POST /api/v1/auth/refresh` for an access token. Records an `oidc_login_success` audit event. Failures redirect to `/login?error=<code>` with one of `sso_denied`, `sso_state`, `sso_failed` or `sso_provision`.
Errors: 404 if OIDC is not enabled.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `code` | query | `string` \| `null` | no | Authorization code returned by the OIDC provider. |
| `state` | query | `string` \| `null` | no | State value echoed by the provider; must match the `oidc_state` cookie. |
| `error` | query | `string` \| `null` | no | Error code returned by the provider when the user denied or the login failed. |
| `oidc_state` | cookie | `string` \| `null` | no | State cookie set by `/oidc/login`. |
| `oidc_pkce` | cookie | `string` \| `null` | no | PKCE verifier cookie set by `/oidc/login`. |
| `oidc_nonce` | cookie | `string` \| `null` | no | Nonce cookie set by `/oidc/login`. |

| Status | Description | Body |
| --- | --- | --- |
| 302 | Redirect to the SPA; sets the refresh cookie, or `/login?error=<code>` on failure. | none |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-oidc-login"></a>
#### `GET /api/v1/auth/oidc/login`

**Start OIDC login** (`oidc_login`)

Auth: None (public)

Public. Redirects the browser (302) to the configured OIDC provider and sets short-lived state, PKCE and nonce cookies.
Errors: 404 if OIDC is not enabled; 502 if the provider cannot be reached.

| Status | Description | Body |
| --- | --- | --- |
| 302 | Redirect to the OIDC provider; sets short-lived state cookies. | none |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 502 | Bad Gateway | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-providers"></a>
#### `GET /api/v1/auth/providers`

**List login providers** (`get_providers`)

Auth: None (public)

Public. Reports which authentication providers are enabled and whether first-run setup is still open (`setup_required`, true only while no user exists). The login page uses it to decide what to offer.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ProvidersResponse`](#schema-providersresponse) |

<a id="op-refresh-token"></a>
#### `POST /api/v1/auth/refresh`

**Refresh the access token** (`refresh_token`)

Auth: None (public)

Public, authenticated by the refresh cookie. Mints a new access token and rotates the refresh token on every call. A missing, expired, revoked or replayed refresh token returns 401, which clients should treat as a logout.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `akashic_refresh` | cookie | `string` \| `null` | no | HttpOnly refresh cookie set at login. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`TokenResponse`](#schema-tokenresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-scopes"></a>
#### `GET /api/v1/scopes`

**List scopes and role bundles** (`list_scopes`)

Auth: `userJWT`

Every scope the API defines, and the scopes each role grants. Clients use it to build token pickers. Requires authentication but no particular scope. Errors: 401 for missing or invalid credentials.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ScopeCatalog`](#schema-scopecatalog) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### tokens

Personal API tokens: create, narrow and revoke your own; admins manage everyone's.

- `GET /api/v1/admin/tokens`: List every user's API tokens
- `DELETE /api/v1/admin/tokens/{token_id}`: Revoke any user's API token
- `GET /api/v1/me/tokens`: List your API tokens
- `POST /api/v1/me/tokens`: Create an API token
- `DELETE /api/v1/me/tokens/{token_id}`: Revoke an API token
- `PATCH /api/v1/me/tokens/{token_id}`: Rename or narrow an API token

<a id="op-list-all-tokens"></a>
#### `GET /api/v1/admin/tokens`

**List every user's API tokens** (`list_all_tokens`)

Auth: `userJWT`, scope `tokens:admin`

Every user's tokens with owner username, newest first. Requires scope `tokens:admin`.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `user_id` | query | `string` (`uuid`) \| `null` | no | Only tokens owned by this user. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`AdminApiTokenResponse`](#schema-adminapitokenresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-revoke-any-token"></a>
#### `DELETE /api/v1/admin/tokens/{token_id}`

**Revoke any user's API token** (`revoke_any_token`)

Auth: `userJWT`, scope `tokens:admin`

Revokes any user's token. Idempotent: an already revoked token succeeds with no change and no audit event. Otherwise records an `api_token_revoked` audit event with `by_admin: true`.
Requires scope `tokens:admin`. Errors: 404 if the token does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token_id` | path | `string` (`uuid`) | yes | Token id. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-my-tokens"></a>
#### `GET /api/v1/me/tokens`

**List your API tokens** (`list_my_tokens`)

Auth: `userJWT`, scope `tokens:read`

Your own tokens, newest first, including revoked and expired ones. The token secret is never returned. Requires scope `tokens:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`ApiTokenResponse`](#schema-apitokenresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-create-my-token"></a>
#### `POST /api/v1/me/tokens`

**Create an API token** (`create_my_token`)

Auth: `userJWT`, scope `tokens:write`

Returns the token once; it cannot be retrieved later. Its scopes must lie within yours. A token limited to sources (`source_ids`) cannot hold write scopes on other resources. Records an `api_token_created` audit event.
Requires scope `tokens:write` and an interactive login session: an API token cannot create tokens (403 `interactive_session_required`).
Errors: 422 for unknown scopes, scopes beyond your own, write scopes on a source-limited token, unknown sources or an `expires_at` in the past.

**Request body** (required)
- `application/json`: [`ApiTokenCreate`](#schema-apitokencreate)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`ApiTokenCreated`](#schema-apitokencreated) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-revoke-my-token"></a>
#### `DELETE /api/v1/me/tokens/{token_id}`

**Revoke an API token** (`revoke_my_token`)

Auth: `userJWT`, scope `tokens:write`

Revokes one of your own tokens. Idempotent: revoking an already revoked token succeeds without changing it and without recording an audit event; otherwise records an `api_token_revoked` audit event.
Requires scope `tokens:write`. Errors: 404 if the token does not exist or is not yours.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token_id` | path | `string` (`uuid`) | yes | Token id. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-update-my-token"></a>
#### `PATCH /api/v1/me/tokens/{token_id}`

**Rename or narrow an API token** (`update_my_token`)

Auth: `userJWT`, scope `tokens:write`

Scopes can only be narrowed, never widened; to widen, create a new token. Records an `api_token_updated` audit event.
Requires scope `tokens:write` and an interactive login session (403 `interactive_session_required` for an API token).
Errors: 404 if the token does not exist or is not yours; 409 if it is revoked; 422 for unknown scopes or scopes beyond the token's current set.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token_id` | path | `string` (`uuid`) | yes | Token id. |

**Request body** (required)
- `application/json`: [`ApiTokenUpdate`](#schema-apitokenupdate)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ApiTokenResponse`](#schema-apitokenresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### users

User accounts and registration.

- `GET /api/v1/users`: List users
- `POST /api/v1/users/create`: Create a user
- `POST /api/v1/users/login`: Log in with a password
- `GET /api/v1/users/me`: Get the calling user
- `POST /api/v1/users/register`: Register the first admin
- `PATCH /api/v1/users/{user_id}`: Change a user's role

<a id="op-list-users"></a>
#### `GET /api/v1/users`

**List users** (`list_users`)

Auth: `userJWT`, scope `users:read`

Every user account, ordered by username, with its role. Password hashes are never returned. Not paginated.
Requires scope `users:read`. Not available to source-limited tokens.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`UserResponse`](#schema-userresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-create-user"></a>
#### `POST /api/v1/users/create`

**Create a user** (`create_user`)

Auth: `userJWT`, scope `users:write`

Creates a new local account with the given role (`viewer` unless `role` says otherwise).
Requires scope `users:write`. Errors: 409 if the username is taken; 422 for an invalid username or password (3-64 character username, 8-128 character password of at most 72 UTF-8 bytes).

**Request body** (required)
- `application/json`: [`UserCreate`](#schema-usercreate)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`UserResponse`](#schema-userresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-login"></a>
#### `POST /api/v1/users/login`

**Log in with a password** (`login`)

Auth: None (public)

Verifies a local username and password. Returns a short-lived JWT access token and sets the `akashic_refresh` HttpOnly cookie (scoped to `/api/v1/auth`) that `POST /auth/refresh` exchanges for new access tokens. Public.
Rate limited to 10 attempts per minute per username. Errors: 401 for an unknown user or wrong password; 422 for a malformed body; 429 when rate limited.

**Request body** (required)
- `application/json`: [`UserLogin`](#schema-userlogin)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`TokenResponse`](#schema-tokenresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 429 | Too many requests. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-me"></a>
#### `GET /api/v1/users/me`

**Get the calling user** (`get_me`)

Auth: `userJWT`

The authenticated user and the effective scopes of the credential used. Requires authentication but no particular scope.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`MeResponse`](#schema-meresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-register"></a>
#### `POST /api/v1/users/register`

**Register the first admin** (`register`)

Auth: None (public)

Bootstrap endpoint: creates the very first account, which becomes an `admin`. Public, but only works while no user exists; afterwards registration is closed and accounts are created by an admin with `POST /users/create`. Concurrent calls are serialized so only one becomes admin.
Errors: 403 once any user exists; 409 if the username is taken; 422 for an invalid username or password (3-64 character username, 8-128 character password of at most 72 UTF-8 bytes).

**Request body** (required)
- `application/json`: [`UserCreate`](#schema-usercreate)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`UserResponse`](#schema-userresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-update-user-role"></a>
#### `PATCH /api/v1/users/{user_id}`

**Change a user's role** (`update_user_role`)

Auth: `userJWT`, scope `users:write`

Sets a user's role (`viewer` or `admin`) and/or whether it is pinned. Setting `role` pins the user unless `role_pinned` is also given; a pinned role is never changed by IdP group mapping (`oidc_admin_groups`, `ldap_admin_groups`). `role_pinned: false` hands the role back to the IdP at the next login. Takes effect immediately, API tokens included. Recorded as a `user_role_changed` audit event.
Requires scope `users:write`. Errors: 404 for an unknown user; 409 when the change would leave no admin; 422 when neither `role` nor `role_pinned` is given.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `user_id` | path | `string` (`uuid`) | yes |  |

**Request body** (required)
- `application/json`: [`UserRoleUpdate`](#schema-userroleupdate)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`UserResponse`](#schema-userresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### sources

Indexed storage sources: connections, scans, reachability and scanner allow-lists.

- `GET /api/v1/sources`: List sources
- `POST /api/v1/sources`: Create a source
- `POST /api/v1/sources/test`: Test a source connection before saving
- `GET /api/v1/sources/{source_id}`: Get a source
- `DELETE /api/v1/sources/{source_id}`: Delete a source
- `PATCH /api/v1/sources/{source_id}`: Update a source
- `PATCH /api/v1/sources/{source_id}/allowed-scanners`: Set the scanners allowed for a source
- `GET /api/v1/sources/{source_id}/audit`: Source audit timeline
- `GET /api/v1/sources/{source_id}/entry-count`: Count a source's entries
- `GET /api/v1/sources/{source_id}/orphan-match-count`: Count recoverable orphans
- `GET /api/v1/sources/{source_id}/reachability-history`: Source reachability history
- `GET /api/v1/sources/{source_id}/reachability-summary`: Source reachability summary
- `POST /api/v1/sources/{source_id}/reattach-orphans`: Re-attach orphaned entries
- `GET /api/v1/sources/{source_id}/scanner-reachability`: Scanner reachability for a source
- `POST /api/v1/sources/{source_id}/test-scanners`: Test a source from its scanners

<a id="op-list-sources"></a>
#### `GET /api/v1/sources`

**List sources** (`list_sources`)

Auth: `userJWT`, scope `sources:read`

Every visible source ordered by name, in a lean shape: no connection config, with a server-rendered `summary` line for display. Not paginated. Fetch one source for its full (secret-masked) configuration.
Requires scope `sources:read`. Source-limited API tokens only see their permitted sources.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`SourceListResponse`](#schema-sourcelistresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-create-source"></a>
#### `POST /api/v1/sources`

**Create a source** (`create_source`)

Auth: `userJWT`, scope `sources:write`

Registers a storage source. `connection_config` holds the type's connection settings; attach a `host_id` (SMB, NFS and S3 sources) and/or a `credential_profile_id` to inherit connection settings and credentials, which `connection_config` overrides. For OAuth-backed types pass `oauth_credential_id` inside `connection_config` to attach an unattached credential. `is_removable` is inferred from the type and path when omitted. The scan-control values are validated. Publishes a `source.created` event and records a `source_created` audit event (secrets omitted).
Requires scope `sources:write`. Not available to source-limited tokens, since a new source is outside any limit. Errors: 400 for a masked `***` value in the config, a host or profile of a different type, a hostless type given a host, an invalid SMB password setup, out-of-range scan controls or an invalid `mirror_of`; 404 for an unknown `host_id`, `credential_profile_id`, `oauth_credential_id` or `mirror_of` target; 409 if the OAuth credential is attached elsewhere; 422 for a malformed body.

**Request body** (required)
- `application/json`: [`SourceCreate`](#schema-sourcecreate)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`SourceResponse`](#schema-sourceresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-post-test"></a>
#### `POST /api/v1/sources/test`

**Test a source connection before saving** (`post_test`)

Auth: `userJWT`, scope `sources:write`

Runs the connection probe for a source that does not exist yet, exactly as the "Test connection" button does. A `credential_profile_id` and `host_id` are merged under the inline `connection_config` with the same precedence a real scan uses (host profile, host, source profile, inline). Never echoes credentials. Records a `source_test_run` audit event holding only an allow-list of non-secret connection fields and the result.
A probe that fails is still a 200 response with `ok: false`; inspect `step` and `error`.
Requires scope `sources:write`. Not available to source-limited tokens, because it probes arbitrary endpoints. Errors: 422 for a malformed body.

**Request body** (required)
- `application/json`: [`TestSourceRequest`](#schema-testsourcerequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`TestResult`](#schema-testresult) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-source"></a>
#### `GET /api/v1/sources/{source_id}`

**Get a source** (`get_source`)

Auth: `userJWT`, scope `sources:read`

A source with its connection configuration, scan settings and status. Values of secret-looking keys (password, secret, key, token, credentials, private_key) are masked as `***`.
Requires scope `sources:read`. Source-limited API tokens only see their permitted sources. Errors: 404 if the source does not exist or is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`SourceResponse`](#schema-sourceresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-delete-source"></a>
#### `DELETE /api/v1/sources/{source_id}`

**Delete a source** (`delete_source`)

Auth: `userJWT`, scope `sources:write`

Deletes the source. By default its entries are kept as orphans with no source (still searchable, content unavailable) and can later be re-attached to another source with `POST /sources/{source_id}/reattach-orphans`; with `purge_entries` they are deleted too. Its scans are kept, detached from the source. The search index is updated, a `source.deleted` event is published and a `source_deleted` audit event records the details. Deleting an Immich or Paperless source removes its mappings from every source's `mirror_of` and unlinks the files that were linked to it.
Requires scope `sources:write`. Errors: 404 if the source does not exist or is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source to delete. |
| `purge_entries` | query | `boolean` | no | Also delete every indexed entry of the source instead of keeping them as orphans. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-update-source"></a>
#### `PATCH /api/v1/sources/{source_id}`

**Update a source** (`update_source`)

Auth: `userJWT`, scope `sources:write`

Changes the fields that are sent; omitted fields are untouched. `connection_config` is merged key by key into the stored one, and a secret key sent as `***` keeps its stored value. The new state is validated as for creation. Publishes a `source.updated` event and records a `source_updated` audit event with the before and after of what changed (secrets omitted). A source-limited token may change only: exclude_patterns, is_removable, max_parallel_scanners, name, preferred_pool, scan_chunk_size, scan_schedule. Changing `connection_config`, `credential_profile_id` or `host_id` is refused with 403 `source_limited`.
Requires scope `sources:write`. Errors: 400 for a host or profile of a different type, a hostless type given a host, removing a required host, a masked `***` in a non-secret key, an invalid SMB password setup, out-of-range scan controls or an invalid `mirror_of`; 404 if the source (or host, profile or `mirror_of` target) does not exist or is outside the token's sources; 422 for a malformed body. A changed `connection_config.mirror_of` is normalised and starts a background mirror-link pass.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source to update. |

**Request body** (required)
- `application/json`: [`SourceUpdate`](#schema-sourceupdate)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`SourceResponse`](#schema-sourceresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-patch-source-allowed-scanners"></a>
#### `PATCH /api/v1/sources/{source_id}/allowed-scanners`

**Set the scanners allowed for a source** (`patch_source_allowed_scanners`)

Auth: `userJWT`, scope `sources:write`

Makes exactly the listed scanners eligible to scan this source by editing each scanner's own allow-list: listed scanners gain the source, other scanners lose it (a scanner that could scan every source is narrowed to all others). Idempotent. Records a `source_allowed_scanners_updated` audit event.
Requires scope `sources:write`. Not available to source-limited tokens, since it rewrites other scanners' settings. Errors: 400 for unknown scanner ids; 404 if the source does not exist; 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source to configure. |

**Request body** (required)
- `application/json`: [`AllowedScannersRequest`](#schema-allowedscannersrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`AllowedScannersResponse`](#schema-allowedscannersresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-source-audit"></a>
#### `GET /api/v1/sources/{source_id}/audit`

**Source audit timeline** (`get_source_audit`)

Auth: `userJWT`, scope `sources:read`

Audit events for one source, newest first, including the `source_deleted` event of a source that no longer exists when queried by its former id. Cursor-paginated; `total` is not computed.
Requires scope `sources:read`. Source-limited API tokens only see their permitted sources. Errors: 404 if the source is outside the token's sources; 422 for a malformed `cursor` or invalid parameter.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source id. |
| `cursor` | query | `string` \| `null` | no | Opaque cursor from a previous `next_cursor`. |
| `limit` | query | `integer` | no | Page size. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`Page_AuditEventOut_`](#schema-page-auditeventout-) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-source-entry-count"></a>
#### `GET /api/v1/sources/{source_id}/entry-count`

**Count a source's entries** (`get_source_entry_count`)

Auth: `userJWT`, scope `sources:read`

Number of entries (files and directories, excluding soft-deleted ones) the source holds; shows what deleting it with `purge_entries` would remove.
Requires scope `sources:read`. Source-limited API tokens only see their permitted sources. Errors: 404 if the source is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`SourceEntryCount`](#schema-sourceentrycount) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-orphan-match-count"></a>
#### `GET /api/v1/sources/{source_id}/orphan-match-count`

**Count recoverable orphans** (`get_orphan_match_count`)

Auth: `userJWT`, scope `sources:write`

How many orphaned entries (entries whose source was deleted) share a path with an entry of this source, i.e. what `POST /sources/{source_id}/reattach-orphans` could recover.
Requires scope `sources:write`. Not available to source-limited tokens, since orphans belong to no source.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source to match orphans against. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`OrphanMatchCount`](#schema-orphanmatchcount) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-source-reachability-history"></a>
#### `GET /api/v1/sources/{source_id}/reachability-history`

**Source reachability history** (`get_source_reachability_history`)

Auth: `userJWT`, scope `sources:read`

Recent probe outcomes for the source, grouped by scanner, newest first. Not paginated.
Requires scope `sources:read`. Source-limited API tokens only see their permitted sources. Errors: 404 if the source does not exist or is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source id. |
| `limit` | query | `integer` | no | Outcomes per scanner; values are clamped to 1-20. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ReachabilityHistory`](#schema-reachabilityhistory) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-source-reachability-summary"></a>
#### `GET /api/v1/sources/{source_id}/reachability-summary`

**Source reachability summary** (`get_source_reachability_summary`)

Auth: `userJWT`, scope `sources:read`

Whether the source is reachable, from the most recent probe by any scanner, or a completed scan that finished after it (a successful scan proves reachability). `ok` is null when there is no data yet.
Requires scope `sources:read`. Source-limited API tokens only see their permitted sources. Errors: 404 if the source does not exist or is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ReachabilitySummary`](#schema-reachabilitysummary) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-reattach-orphans"></a>
#### `POST /api/v1/sources/{source_id}/reattach-orphans`

**Re-attach orphaned entries** (`reattach_orphans`)

Auth: `userJWT`, scope `sources:write`

Moves orphaned entries (source deleted earlier, entries kept) into this source where their path, name and kind match a freshly scanned entry (`path_and_hash` also requires the same content hash). The orphan keeps its tags, version history and audit trail and the duplicate fresh entry is removed. With `dry_run` true (the default) nothing changes and the counts show what would happen: `conflicts` and `ambiguous` matches are not moved. The search index is updated and a `source_orphans_reattached` audit event is recorded.
Requires scope `sources:write`. Not available to source-limited tokens. Errors: 400 for an unknown strategy; 404 if the source does not exist; 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source to attach orphans to. |

**Request body** (required)
- `application/json`: [`ReattachRequest`](#schema-reattachrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ReattachResponse`](#schema-reattachresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-source-scanner-reachability"></a>
#### `GET /api/v1/sources/{source_id}/scanner-reachability`

**Scanner reachability for a source** (`list_source_scanner_reachability`)

Auth: `userJWT`, scope `sources:read`

Every registered scanner with whether it may claim this source, whether it is online (seen within 2 minutes) and its latest probe result for the source. Does not run any probes; use `POST /sources/{source_id}/test-scanners` for that.
Requires scope `sources:read`. Source-limited API tokens only see their permitted sources. Errors: 404 if the source does not exist or is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`ScannerReachabilityRow`](#schema-scannerreachabilityrow) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-test-source-scanners"></a>
#### `POST /api/v1/sources/{source_id}/test-scanners`

**Test a source from its scanners** (`test_source_scanners`)

Auth: `userJWT`, scope `sources:write`

Asks scanners to probe the source from their own network position with their own credentials. Without `scanner_ids`, every enabled scanner permitted for this source (pool and allow-list) that was seen in the last two minutes is asked. Answers arriving within 5 seconds are returned in place; slower ones are `pending: true` and arrive later on the reachability event stream. Results are stored in the reachability history.
Requires scope `sources:write`. Errors: 404 if the source does not exist or is outside the token's sources; 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source to probe. |

**Request body**
- `application/json`: [`TestScannersRequest`](#schema-testscannersrequest) \| `null`

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`TestScannersResponse`](#schema-testscannersresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### hosts

Network hosts (SMB, NFS, S3) that group sources: connection settings, share discovery, reachability checks and scanner allow-lists.

- `GET /api/v1/hosts`: List hosts
- `POST /api/v1/hosts`: Create a host
- `GET /api/v1/hosts/{host_id}`: Get a host
- `DELETE /api/v1/hosts/{host_id}`: Delete a host
- `PATCH /api/v1/hosts/{host_id}`: Update a host
- `POST /api/v1/hosts/{host_id}/add-shares`: Add shares as sources
- `PATCH /api/v1/hosts/{host_id}/allowed-scanners`: Set allowed scanners for a host
- `POST /api/v1/hosts/{host_id}/list-shares`: List shares on a host
- `POST /api/v1/hosts/{host_id}/online-check`: Check whether a host is online
- `GET /api/v1/hosts/{host_id}/scanner-reachability-summary`: Summarise scanner reachability for a host
- `POST /api/v1/hosts/{host_id}/test-shares`: Test reachability of all shares

<a id="op-list-hosts"></a>
#### `GET /api/v1/hosts`

**List hosts** (`list_hosts`)

Auth: `userJWT`, scope `hosts:read`

Every host, with `source_count` so a list view can show how many shares are attached. Secrets are masked. Requires scope `hosts:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`HostResponse`](#schema-hostresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-create-host"></a>
#### `POST /api/v1/hosts`

**Create a host** (`create_host`)

Auth: `userJWT`, scope `hosts:write`

Creates a network host (SMB, NFS or S3) that sources attach to. Its connection config and credential profile are inherited by those sources, and the optional scan- distribution limits apply to every attached source that does not set its own. Secrets in the response are masked. Records a `host_created` audit event.
Requires scope `hosts:write`.
Errors: 400 for an unsupported `type`, a masked-secret placeholder in `connection_config`, out-of-range scan controls, or a credential profile of a different type; 404 if the credential profile does not exist; 409 if the name is already used.

**Request body** (required)
- `application/json`: [`HostCreate`](#schema-hostcreate)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`HostResponse`](#schema-hostresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-host"></a>
#### `GET /api/v1/hosts/{host_id}`

**Get a host** (`get_host`)

Auth: `userJWT`, scope `hosts:read`

Requires scope `hosts:read`. Errors: 404 if the host does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `host_id` | path | `string` (`uuid`) | yes | Host id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`HostResponse`](#schema-hostresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-delete-host"></a>
#### `DELETE /api/v1/hosts/{host_id}`

**Delete a host** (`delete_host`)

Auth: `userJWT`, scope `hosts:write`

Deletes a host that has no sources attached. Records a `host_deleted` audit event.
Requires scope `hosts:write`.
Errors: 404 if the host does not exist; 409 if sources are still attached (delete or detach them first).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `host_id` | path | `string` (`uuid`) | yes | Host id. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-update-host"></a>
#### `PATCH /api/v1/hosts/{host_id}`

**Update a host** (`update_host`)

Auth: `userJWT`, scope `hosts:write`

Partial update. In `connection_config`, masked secret values (`***`) keep the stored secret. Only the scan controls present in the body are validated, and an explicit null clears one so it falls back to the default. The type cannot change. Records a `host_updated` audit event with the changed fields when something changed.
Requires scope `hosts:write`.
Errors: 400 for out-of-range scan controls or a credential profile of a different type; 404 if the host or credential profile does not exist; 409 if the new name is already used.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `host_id` | path | `string` (`uuid`) | yes | Host id. |

**Request body** (required)
- `application/json`: [`HostUpdate`](#schema-hostupdate)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`HostResponse`](#schema-hostresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-add-host-shares"></a>
#### `POST /api/v1/hosts/{host_id}/add-shares`

**Add shares as sources** (`add_host_shares`)

Auth: `userJWT`, scope `hosts:write`

Creates one source per entry in `shares`, attached to the host. Each `share` becomes the share key of the source's connection config (`share` for SMB, `export_path` for NFS, `bucket` for S3); credentials stay on the host. The optional settings apply to every created source. Entries whose share is already attached to the host (SMB names compare case-insensitively, NFS ignores a trailing slash), repeated in the request, or whose source name is taken are skipped and counted in `skipped`. Records a `host_shares_batch_added` audit event when at least one source is created.
Requires scope `hosts:write`.
Errors: 400 for host types without shares or `max_parallel_scanners` outside 1-16; 404 if the host does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `host_id` | path | `string` (`uuid`) | yes | Host id. |

**Request body** (required)
- `application/json`: [`AddSharesRequest`](#schema-addsharesrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`AddSharesResponse`](#schema-addsharesresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-patch-host-allowed-scanners"></a>
#### `PATCH /api/v1/hosts/{host_id}/allowed-scanners`

**Set allowed scanners for a host** (`patch_host_allowed_scanners`)

Auth: `userJWT`, scope `hosts:write`

Applies one allowed-scanner set to every source attached to the host: scanners in `scanner_ids` are added to each source's allow-list and all other scanners are removed from it. Idempotent. Records a `host_allowed_scanners_applied` audit event.
Requires scope `hosts:write`.
Errors: 400 if any scanner id is unknown; 404 if the host does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `host_id` | path | `string` (`uuid`) | yes | Host id. |

**Request body** (required)
- `application/json`: [`HostAllowedScannersRequest`](#schema-hostallowedscannersrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`HostAllowedScannersResponse`](#schema-hostallowedscannersresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-host-shares"></a>
#### `POST /api/v1/hosts/{host_id}/list-shares`

**List shares on a host** (`list_host_shares`)

Auth: `userJWT`, scope `hosts:write`

Enumerates the shares, exports or buckets visible to the host's credentials. On failure the response is still 200 with an empty list and the failing `step` and `error`. Records a `host_shares_listed` audit event. `already_added` lists the shares that already have a source on this host.
Requires scope `hosts:write`.
Errors: 400 for host types without shares; 404 if the host does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `host_id` | path | `string` (`uuid`) | yes | Host id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ListSharesResponse`](#schema-listsharesresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-host-online-check"></a>
#### `POST /api/v1/hosts/{host_id}/online-check`

**Check whether a host is online** (`host_online_check`)

Auth: `userJWT`, scope `hosts:write`

A network-level probe from the API process: a TCP connect to the SMB (445) or NFS (2049) port, or a request to the S3 endpoint. No credentials are used and no shares are listed, so it separates DNS and firewall problems from credential problems. Credentialed reachability is reported by scanners (see `POST /sources/{source_id}/test-scanners`). Records a `host_online_check` audit event.
Requires scope `hosts:write`. Errors: 404 if the host does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `host_id` | path | `string` (`uuid`) | yes | Host id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`OnlineCheckResponse`](#schema-onlinecheckresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-host-scanner-reachability-summary"></a>
#### `GET /api/v1/hosts/{host_id}/scanner-reachability-summary`

**Summarise scanner reachability for a host** (`host_scanner_reachability_summary`)

Auth: `userJWT`, scope `hosts:read`

Per scanner, how many of the host's attached sources it is currently allowed to claim, can reach, cannot reach, or has not been probed against, based on each pair's latest probe result. Feeds the host-level scanner eligibility checklist.
Requires scope `hosts:read`. Errors: 404 if the host does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `host_id` | path | `string` (`uuid`) | yes | Host id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`HostScannerSummaryRow`](#schema-hostscannersummaryrow) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-test-host-shares"></a>
#### `POST /api/v1/hosts/{host_id}/test-shares`

**Test reachability of all shares** (`test_host_shares`)

Auth: `userJWT`, scope `hosts:write`

For every share (source) attached to the host, dispatches a credentialed probe to each online scanner permitted to claim it and returns one row per (source, scanner). The call waits up to 5 seconds per source; rows for scanners that have not answered yet have `pending: true` and their results arrive later on the source-reachability WebSocket channel. Sources with no eligible scanner produce no rows.
Requires scope `hosts:write`. Errors: 404 if the host does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `host_id` | path | `string` (`uuid`) | yes | Host id. |

**Request body**
- `application/json`: [`TestSharesRequest`](#schema-testsharesrequest) \| `null`

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`TestSharesResponse`](#schema-testsharesresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### credential-profiles

Reusable, encrypted connection credentials.

- `GET /api/v1/credential-profiles`: List credential profiles
- `POST /api/v1/credential-profiles`: Create a credential profile
- `GET /api/v1/credential-profiles/{profile_id}`: Get a credential profile
- `DELETE /api/v1/credential-profiles/{profile_id}`: Delete a credential profile
- `PATCH /api/v1/credential-profiles/{profile_id}`: Update a credential profile

<a id="op-list-profiles"></a>
#### `GET /api/v1/credential-profiles`

**List credential profiles** (`list_profiles`)

Auth: `userJWT`, scope `credentials:list`

Name, type and description of every profile, sorted by name. Credentials are never included. Requires scope `credentials:list`.
Errors: 400 for an unsupported `type` filter.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `type` | query | `string` \| `null` | no | Only profiles of this type: `smb`, `nfs` or `s3`. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`CredentialProfileSummary`](#schema-credentialprofilesummary) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-create-profile"></a>
#### `POST /api/v1/credential-profiles`

**Create a credential profile** (`create_profile`)

Auth: `userJWT`, scope `credentials:write`

Creates a reusable credential set that hosts and sources can reference. Credentials are stored encrypted and masked as `***` in every response. SMB profiles must have a non- empty password. Records a `credential_profile_created` audit event.
Requires scope `credentials:write`.
Errors: 400 for an unsupported `type`; 409 if the name is already used; 422 for an SMB profile without a password.

**Request body** (required)
- `application/json`: [`CredentialProfileCreate`](#schema-credentialprofilecreate)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`CredentialProfileResponse`](#schema-credentialprofileresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-profile"></a>
#### `GET /api/v1/credential-profiles/{profile_id}`

**Get a credential profile** (`get_profile`)

Auth: `userJWT`, scope `credentials:read`

Returns the profile with its credentials masked as `***`. Requires scope `credentials:read`.
Errors: 404 if the profile does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `profile_id` | path | `string` (`uuid`) | yes | Credential profile id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`CredentialProfileResponse`](#schema-credentialprofileresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-delete-profile"></a>
#### `DELETE /api/v1/credential-profiles/{profile_id}`

**Delete a credential profile** (`delete_profile`)

Auth: `userJWT`, scope `credentials:write`

Deletes the profile. Records a `credential_profile_deleted` audit event.
Requires scope `credentials:write`.
Errors: 404 if the profile does not exist; 409 if hosts or sources still reference it (reassign or unlink them first).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `profile_id` | path | `string` (`uuid`) | yes | Credential profile id. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-update-profile"></a>
#### `PATCH /api/v1/credential-profiles/{profile_id}`

**Update a credential profile** (`update_profile`)

Auth: `userJWT`, scope `credentials:write`

Partial update; the type cannot change. In `credentials`, a value of `***` leaves the stored value untouched, so a masked response can be edited and sent back. When credentials actually change, a fresh reachability probe is queued in the background for every source using the profile. Records a `credential_profile_updated` audit event.
Requires scope `credentials:write`.
Errors: 404 if the profile does not exist; 409 if the new name is already used; 422 if an SMB profile would end up without a password.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `profile_id` | path | `string` (`uuid`) | yes | Credential profile id. |

**Request body** (required)
- `application/json`: [`CredentialProfileUpdate`](#schema-credentialprofileupdate)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`CredentialProfileResponse`](#schema-credentialprofileresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### oauth

OAuth authorization flows for cloud-drive sources.

- `GET /api/v1/oauth/callback`: OAuth redirect target
- `GET /api/v1/oauth/credentials`: List OAuth credentials
- `DELETE /api/v1/oauth/credentials/{credential_id}`: Delete an OAuth credential
- `POST /api/v1/oauth/credentials/{credential_id}/refresh`: Refresh an OAuth credential now
- `GET /api/v1/oauth/providers`: List OAuth providers
- `PUT /api/v1/oauth/providers/{provider}`: Configure an OAuth provider
- `DELETE /api/v1/oauth/providers/{provider}`: Remove an OAuth provider configuration
- `POST /api/v1/oauth/start`: Start an OAuth authorization

<a id="op-callback"></a>
#### `GET /api/v1/oauth/callback`

**OAuth redirect target** (`callback`)

Auth: None (public)

The `redirect_uri` the provider sends the browser back to. Verifies the signed `state` and that the same user and browser session that started the flow is completing it, exchanges `code` for tokens and stores the credential (or, in `test` mode, verifies and discards it). Always answers 200 with a small HTML page that posts the result to `window.opener` (restricted to the configured callback origin) and closes itself; failures, including rate limiting, are reported on the page rather than as HTTP errors.
Public: the signed `state` is the credential. Rate limited to 10 requests per minute per client.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `code` | query | `string` \| `null` | no | Authorization code from the provider. |
| `state` | query | `string` \| `null` | no | Signed state from `POST /oauth/start`. |
| `error` | query | `string` \| `null` | no | Provider error code, when the user denied access. |
| `error_description` | query | `string` \| `null` | no | Provider's human-readable error. |
| `akashic_refresh` | cookie | `string` \| `null` | no | Browser refresh cookie, compared with the session that started the flow. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | HTML page that posts the result to `window.opener` and closes itself. | `text/html`: `string` |
| 422 | Request failed validation. | `text/html`: [`ErrorResponse`](#schema-errorresponse) |
| 429 | Too many requests. | `text/html`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-credentials"></a>
#### `GET /api/v1/oauth/credentials`

**List OAuth credentials** (`list_credentials`)

Auth: `userJWT`, scope `credentials:read`

Every stored OAuth credential, newest first, with the connected account and the source it is attached to. Tokens are never returned.
Requires scope `credentials:read`. Not available to source-limited tokens.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`OAuthCredentialSummary`](#schema-oauthcredentialsummary) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-delete-credential"></a>
#### `DELETE /api/v1/oauth/credentials/{credential_id}`

**Delete an OAuth credential** (`delete_credential`)

Auth: `userJWT`, scope `credentials:write`

Forgets a stored credential; a source that uses it can no longer be scanned until it is reconnected. Idempotent: succeeds when it does not exist.
Requires scope `credentials:write`.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `credential_id` | path | `string` (`uuid`) | yes | Credential to delete. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-force-refresh"></a>
#### `POST /api/v1/oauth/credentials/{credential_id}/refresh`

**Refresh an OAuth credential now** (`force_refresh`)

Auth: `userJWT`, scope `credentials:write`

Discards the cached access token and mints a new one from the stored refresh token, to confirm the credential still works. Returns the new access token.
Requires scope `credentials:write`. Errors: 404 if the credential does not exist; 502 if the provider refused the refresh.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `credential_id` | path | `string` (`uuid`) | yes | Credential to refresh. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`OAuthRefreshResponse`](#schema-oauthrefreshresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 502 | Bad Gateway | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-providers"></a>
#### `GET /api/v1/oauth/providers`

**List OAuth providers** (`list_providers`)

Auth: `userJWT`, scope `credentials:read`

Every OAuth provider the server supports (`google`, `microsoft`, `dropbox`), with whether an app (client id and secret) is configured. Unconfigured providers are returned with empty `client_id` and `redirect_uri` and `has_secret: false`. The secret is never returned.
Requires scope `credentials:read`. Not available to source-limited tokens.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`OAuthAppConfigSummary`](#schema-oauthappconfigsummary) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-upsert-provider"></a>
#### `PUT /api/v1/oauth/providers/{provider}`

**Configure an OAuth provider** (`upsert_provider`)

Auth: `userJWT`, scope `credentials:write`

Creates or updates the OAuth app (client id, client secret, redirect URI) for a provider. The secret is stored encrypted and never returned. On update, omit `client_secret` to keep the stored one.
Requires scope `credentials:write`. Errors: 400 for an unknown provider, or a missing `client_secret` when creating; 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `provider` | path | `string` | yes | Provider key from `GET /oauth/providers`. |

**Request body** (required)
- `application/json`: [`OAuthAppConfigUpsert`](#schema-oauthappconfigupsert)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`OAuthAppConfigSummary`](#schema-oauthappconfigsummary) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-delete-provider"></a>
#### `DELETE /api/v1/oauth/providers/{provider}`

**Remove an OAuth provider configuration** (`delete_provider`)

Auth: `userJWT`, scope `credentials:write`

Forgets the provider's client id and secret. Idempotent: succeeds when nothing is configured. Existing credentials stay but can no longer refresh.
Requires scope `credentials:write`.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `provider` | path | `string` | yes | Provider key. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-start-authorize"></a>
#### `POST /api/v1/oauth/start`

**Start an OAuth authorization** (`start_authorize`)

Auth: `userJWT`, scope `credentials:write`

Builds the provider authorization URL for a source credential and returns it with the signed `state`. Open the URL in a browser window; the provider redirects to `GET /oauth/callback`, which stores the credential. The flow is bound to the caller's browser session (refresh cookie) and the state is short-lived. With `mode` `associate` the credential attaches to `source_id`; with `test` it is verified and then discarded.
Requires scope `credentials:write`. Errors: 400 for an unknown provider or mode; 412 if the provider has no configured app; 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `akashic_refresh` | cookie | `string` \| `null` | no | Browser refresh cookie; binds the flow to the current session. Sent automatically by browsers. |

**Request body** (required)
- `application/json`: [`OAuthStartRequest`](#schema-oauthstartrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`OAuthStartResponse`](#schema-oauthstartresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 412 | Precondition Failed | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### scans

Trigger, cancel and inspect scans.

- `GET /api/v1/scans`: List scans
- `POST /api/v1/scans/trigger`: Trigger a scan
- `GET /api/v1/scans/{scan_id}`: Get a scan
- `POST /api/v1/scans/{scan_id}/cancel`: Cancel a scan
- `GET /api/v1/scans/{scan_id}/log`: Scan log lines

<a id="op-list-scans"></a>
#### `GET /api/v1/scans`

**List scans** (`list_scans`)

Auth: `userJWT`, scope `scans:read`

Scans across the visible sources, newest start first (scans not yet started first). Cursor-paginated; `total` is not computed.
Requires scope `scans:read`. Source-limited API tokens only see scans of their permitted sources. Errors: 404 if `source_id` is outside the token's sources; 422 for a malformed `cursor` or invalid parameter.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | query | `string` (`uuid`) \| `null` | no | Only scans of this source. |
| `status` | query | `string` \| `null` | no | Only scans in this status; several may be given comma separated. Statuses are `pending`, `running`, `completed`, `failed` and `cancelled`. |
| `cursor` | query | `string` \| `null` | no | Opaque cursor from a previous `next_cursor`. |
| `limit` | query | `integer` | no | Page size. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`Page_ScanResponse_`](#schema-page-scanresponse-) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-trigger-scan"></a>
#### `POST /api/v1/scans/trigger`

**Trigger a scan** (`trigger_scan`)

Auth: `userJWT`, scope `scans:write`

Queues a scan for a source, identified by `source_id` or `source_name`. The scan is created `pending` in the pool the source prefers (or any pool) and a scanner agent leases it within seconds; nothing runs on the API host. If the source already has a `pending` or `running` scan, that scan is returned with `created: false` instead of starting a second one. Publishes a scan state event to live subscribers.
Requires scope `scans:write`. Source-limited API tokens may trigger only their permitted sources. Errors: 400 if neither `source_id` nor `source_name` is given; 404 if the source does not exist or is outside the token's sources; 422 for a malformed body.

**Request body** (required)
- `application/json`: [`ScanTriggerRequest`](#schema-scantriggerrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ScanTriggerResponse`](#schema-scantriggerresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-scan"></a>
#### `GET /api/v1/scans/{scan_id}`

**Get a scan** (`get_scan`)

Auth: `userJWT`, scope `scans:read`

One scan with its counters, progress and timing.
Requires scope `scans:read`. Source-limited API tokens only see scans of their permitted sources. Errors: 404 if the scan does not exist or is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ScanResponse`](#schema-scanresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-cancel-scan"></a>
#### `POST /api/v1/scans/{scan_id}/cancel`

**Cancel a scan** (`cancel_scan`)

Auth: `userJWT`, scope `scans:write`

Marks a `pending` or `running` scan `cancelled` with reason `user`, returns its source to `online` and notifies subscribers. The scanner learns of it from the next heartbeat (409) and stops. Idempotent: a scan that already ended is returned unchanged with its current status.
Requires scope `scans:write`. Source-limited API tokens may cancel only scans of their permitted sources. Errors: 404 if the scan does not exist or is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan to cancel. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`CancelResponse`](#schema-cancelresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-log"></a>
#### `GET /api/v1/scans/{scan_id}/log`

**Scan log lines** (`get_log`)

Auth: `userJWT`, scope `scans:read`

Oldest first. `next_cursor` always points just after the last returned line (null only for an empty page), so a client can tail the log by re-sending it; a page shorter than `limit` means the end was reached. Includes structured lines and scanner stderr; filter with `kind`.
Requires scope `scans:read`. Source-limited API tokens only see scans of their permitted sources. Errors: 404 if the scan does not exist or is outside the token's sources; 422 for a malformed `cursor` or invalid parameter.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan whose log to read. |
| `since` | query | `string` (`date-time`) \| `null` | no | Return only entries strictly after this timestamp. Prefer `cursor`, which is lossless when many lines share a timestamp. |
| `cursor` | query | `string` \| `null` | no | Opaque cursor from a previous `next_cursor`; returns lines strictly after it. |
| `kind` | query | `string` | no | `structured` for scanner log lines, `stderr` for process output, `all` for both. |
| `limit` | query | `integer` | no | Maximum lines to return. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`Page_LogEntryOut_`](#schema-page-logentryout-) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### scanners

Scanner agents: enrolment, keys, pools and per-scanner settings.

- `GET /api/v1/scanner-claim-tokens`: List scanner claim tokens
- `POST /api/v1/scanner-claim-tokens`: Create a scanner claim token
- `DELETE /api/v1/scanner-claim-tokens/{token_id}`: Revoke a scanner claim token
- `GET /api/v1/scanner-discovery-requests`: List scanner discovery requests
- `POST /api/v1/scanner-discovery-requests/{discovery_id}/approve`: Approve a scanner discovery request
- `POST /api/v1/scanner-discovery-requests/{discovery_id}/deny`: Deny a scanner discovery request
- `GET /api/v1/scanners`: List scanners
- `POST /api/v1/scanners`: Register a scanner
- `GET /api/v1/scanners/summary`: Count scanners
- `DELETE /api/v1/scanners/{scanner_id}`: Delete a scanner
- `PATCH /api/v1/scanners/{scanner_id}`: Update a scanner
- `POST /api/v1/scanners/{scanner_id}/rotate`: Rotate a scanner key
- `GET /api/v1/scanners/{scanner_id}/source-reachability`: Scanner reachability per source
- `POST /api/v1/scanners/{scanner_id}/test-sources`: Test sources from a scanner

<a id="op-list-claim-tokens"></a>
#### `GET /api/v1/scanner-claim-tokens`

**List scanner claim tokens** (`list_claim_tokens`)

Auth: `userJWT`, scope `scanners:read`

Every claim token, newest first, with its derived status (`active`, `used` or `expired`) and the scanner that used it. The secret is never returned.
Requires scope `scanners:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`ClaimTokenSummary`](#schema-claimtokensummary) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-create-claim-token"></a>
#### `POST /api/v1/scanner-claim-tokens`

**Create a scanner claim token** (`create_claim_token`)

Auth: `userJWT`, scope `scanners:write`

Mints a one-time token that lets a new scanner host enrol itself with `POST /scanners/claim`, and ready-to-paste install snippets. The token is returned once; only its hash is kept. It expires after `ttl_minutes` and can be revoked until used. Scanners created from it get its pool and restrictions. Records a `scanner_claim_token_created` audit event.
Requires scope `scanners:write`. Errors: 400 for unknown source ids or scan types; 422 for a malformed body.

**Request body** (required)
- `application/json`: [`ClaimTokenCreate`](#schema-claimtokencreate)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`ClaimTokenCreated`](#schema-claimtokencreated) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-revoke-claim-token"></a>
#### `DELETE /api/v1/scanner-claim-tokens/{token_id}`

**Revoke a scanner claim token** (`revoke_claim_token`)

Auth: `userJWT`, scope `scanners:write`

Expires an unused token immediately. Records a `scanner_claim_token_revoked` audit event.
Requires scope `scanners:write`. Errors: 404 if the token does not exist; 410 if it was already used.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token_id` | path | `string` (`uuid`) | yes | Claim token to revoke. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 410 | Resource is gone or expired. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-discovery-requests"></a>
#### `GET /api/v1/scanner-discovery-requests`

**List scanner discovery requests** (`list_discovery_requests`)

Auth: `userJWT`, scope `scanners:read`

Pending requests plus those decided in the last 24 hours, newest first, each with its pairing code and the key fingerprint to compare with the scanner host.
Requires scope `scanners:read`. Not available to source-limited tokens.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`DiscoveryAdminSummary`](#schema-discoveryadminsummary) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-approve-discovery"></a>
#### `POST /api/v1/scanner-discovery-requests/{discovery_id}/approve`

**Approve a scanner discovery request** (`approve_discovery`)

Auth: `userJWT`, scope `scanners:write`

Registers the requesting scanner under the given name and pool and tells the waiting scanner it was approved. The scanner is created with no source or scan-type restriction; use `PATCH /scanners/{scanner_id}` to restrict it. Records a `scanner_discovery_approved` audit event.
Requires scope `scanners:write`. Errors: 404 if the request does not exist; 409 if the scanner name is in use; 410 if the request was already decided or has expired; 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `discovery_id` | path | `string` (`uuid`) | yes | Discovery request to approve. |

**Request body** (required)
- `application/json`: [`ApproveBody`](#schema-approvebody)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`DiscoveryApproved`](#schema-discoveryapproved) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 410 | Resource is gone or expired. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-deny-discovery"></a>
#### `POST /api/v1/scanner-discovery-requests/{discovery_id}/deny`

**Deny a scanner discovery request** (`deny_discovery`)

Auth: `userJWT`, scope `scanners:write`

Rejects a pending request, optionally with a reason the scanner sees. Records a `scanner_discovery_denied` audit event.
Requires scope `scanners:write`. Errors: 404 if the request does not exist; 410 if it was already decided or has expired; 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `discovery_id` | path | `string` (`uuid`) | yes | Discovery request to deny. |

**Request body** (required)
- `application/json`: [`DenyBody`](#schema-denybody)

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 410 | Resource is gone or expired. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-scanners"></a>
#### `GET /api/v1/scanners`

**List scanners** (`list_scanners`)

Auth: `userJWT`, scope `scanners:read`

Every registered scanner, ordered by name, with its restrictions, reported version and whether it is online (seen within the last 90 seconds). `effective_config` is the configuration the agent actually receives.
Requires scope `scanners:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`ScannerSummary`](#schema-scannersummary) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-create-scanner"></a>
#### `POST /api/v1/scanners`

**Register a scanner** (`create_scanner`)

Auth: `userJWT`, scope `scanners:write`

Creates a scanner and generates its Ed25519 keypair on the server. The private key is returned once and never stored; deliver it to the scanner host. Prefer claim tokens, which keep the private key on the scanner host.
Requires scope `scanners:write`. Errors: 409 if the name is in use; 422 for a malformed body.

**Request body** (required)
- `application/json`: [`ScannerCreate`](#schema-scannercreate)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`ScannerCreated`](#schema-scannercreated) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-scanners-summary"></a>
#### `GET /api/v1/scanners/summary`

**Count scanners** (`scanners_summary`)

Auth: `userJWT`, scope `scanners:read`

How many scanners are registered and how many are online (seen within the last 90 seconds).
Requires scope `scanners:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ScannerCounts`](#schema-scannercounts) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-delete-scanner"></a>
#### `DELETE /api/v1/scanners/{scanner_id}`

**Delete a scanner** (`delete_scanner`)

Auth: `userJWT`, scope `scanners:write`

Removes the scanner. Scans it was leasing go back to `pending` and unassigned so another scanner can take them. A running agent is refused from then on.
Requires scope `scanners:write`. Errors: 404 if the scanner does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scanner_id` | path | `string` (`uuid`) | yes | Scanner to delete. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-patch-scanner"></a>
#### `PATCH /api/v1/scanners/{scanner_id}`

**Update a scanner** (`patch_scanner`)

Auth: `userJWT`, scope `scanners:write`

Changes a scanner's name, pool, enabled flag, allowed sources and scan types and per-scanner settings. Omitted fields are left unchanged; the `clear_*` flags reset a restriction or setting to unrestricted or inherited. A disabled scanner cannot authenticate.
Requires scope `scanners:write`. Errors: 400 for unknown source ids or scan types (allowed: `incremental`, `full`); 404 if the scanner does not exist; 422 for invalid values.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scanner_id` | path | `string` (`uuid`) | yes | Scanner to update. |

**Request body** (required)
- `application/json`: [`ScannerPatch`](#schema-scannerpatch)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ScannerSummary`](#schema-scannersummary) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-rotate-scanner"></a>
#### `POST /api/v1/scanners/{scanner_id}/rotate`

**Rotate a scanner key** (`rotate_scanner`)

Auth: `userJWT`, scope `scanners:write`

Generates a new keypair for the scanner and returns the new private key once. The old key stops authenticating immediately, so the agent must be reconfigured.
Requires scope `scanners:write`. Errors: 404 if the scanner does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scanner_id` | path | `string` (`uuid`) | yes | Scanner whose key to replace. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ScannerCreated`](#schema-scannercreated) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-scanner-source-reachability"></a>
#### `GET /api/v1/scanners/{scanner_id}/source-reachability`

**Scanner reachability per source** (`list_scanner_source_reachability`)

Auth: `userJWT`, scope `scanners:read`

For one scanner, its latest probe result against every source, and whether it is currently allowed to claim work for it. Sources never probed show `ok: null`. Does not run any probes.
Requires scope `scanners:read`. Errors: 404 if the scanner does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scanner_id` | path | `string` (`uuid`) | yes | Scanner to report on. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`SourceReachabilityRow`](#schema-sourcereachabilityrow) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-test-scanner-sources"></a>
#### `POST /api/v1/scanners/{scanner_id}/test-sources`

**Test sources from a scanner** (`test_scanner_sources`)

Auth: `userJWT`, scope `scanners:write`

Probes sources from this scanner's point of view and stores the results. Without `source_ids`, every source the scanner may claim (allowed list and pool) is tested. Network sources are probed by the API; local sources are delegated to the scanner and wait up to 5 seconds, after which the row is `pending: true`.
Requires scope `scanners:write`. Errors: 404 if the scanner does not exist; 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scanner_id` | path | `string` (`uuid`) | yes | Scanner to probe from. |

**Request body**
- `application/json`: [`TestSourcesRequest`](#schema-testsourcesrequest) \| `null`

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`TestSourcesResponse`](#schema-testsourcesresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### search

Full-text and filtered file search.

- `GET /api/v1/search`: Search files

<a id="op-search"></a>
#### `GET /api/v1/search`

**Search files** (`search`)

Auth: `userJWT`, scope `files:read`

Searches indexed files by name and, where extracted, content. `mode` selects fuzzy full-text matching (served by the search index, with typo tolerance and facet counts), `glob` or `regex` matching against the file name or path (served from the database). The request falls back to the database when the search index is unavailable or a filter predicate cannot be expressed there; `facet_distribution` is then null. Results can be narrowed by source, extension and size, by the `filters` predicate list, and by permission: with `permission_filter` set to `readable` or `writable` only files the caller's linked filesystem identities may read or write are returned (the default is `readable` once the caller has identities bound, otherwise `all`). Each hit carries `dup_count`, the number of other copies within the same visibility.
Offset cursor pagination: pass `next_cursor` back as `cursor`; `total` is an estimate on the index path. Callers allowed to bypass ACL filtering (scope `users:write`) can pass `search_as` to see results exactly as a given filesystem identity would; each use is recorded as a `search_as_used` audit event.
Requires scope `files:read`. Source-limited API tokens only search their permitted sources. Errors: 400 for an invalid extension, regular expression or `filters` value; 403 if `search_as` is used without the bypass scope; 404 if `source_id` is outside the token's sources; 422 for a malformed `search_as`, `cursor` or other parameter.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `q` | query | `string` | no | Search text. Empty returns everything matching the filters. |
| `mode` | query | `"fuzzy"` \| `"glob"` \| `"regex"` | no | `fuzzy` (default) is full-text and typo tolerant; `glob` matches shell wildcards against the name (or the path when the pattern contains `/`); `regex` matches a regular expression against the path. |
| `source_id` | query | `string` (`uuid`) \| `null` | no | Only files in this source. |
| `extension` | query | `string` \| `null` | no | Only files with this extension, without the dot (letters and digits, up to 20). |
| `min_size` | query | `integer` \| `null` | no | Only files at least this many bytes. |
| `max_size` | query | `integer` \| `null` | no | Only files at most this many bytes. |
| `permission_filter` | query | `"all"` \| `"readable"` \| `"writable"` \| `null` | no | `all`, `readable` or `writable`. Defaults to `readable` for callers with bound filesystem identities, otherwise `all`. |
| `search_as` | query | `string` \| `null` | no | Callers allowed to bypass ACL filtering only: a JSON object `{"type": ..., "identifier": ..., "groups": [...]}` (type one of `posix_uid`, `sid`, `nfsv4_principal`, `s3_canonical`) to search as that identity. |
| `filters` | query | `string` \| `null` | no | Predicate list as base64url-encoded JSON, for example `[{"kind": "extension", "value": "pdf"}]`. Kinds: `extension`, `source`, `owner`, `principal`, `mime`, `size`, `mtime`, `path`, `tag`, `link` and `domain_metadata`. Without a `link` predicate, files linked to an Immich or Paperless entry the caller can also see are hidden; `link` (`any` or a role) returns linked files. All predicates are ANDed with the other parameters. |
| `sort` | query | `"relevance"` \| `"name"` \| `"size"` \| `"mtime"` | no | Sort key; `relevance` (default) uses the full-text ranking. |
| `order` | query | `"asc"` \| `"desc"` | no | Sort direction; ignored for `relevance`. |
| `cursor` | query | `string` \| `null` | no | Opaque cursor from a previous `next_cursor`. |
| `limit` | query | `integer` | no | Page size. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`SearchPage`](#schema-searchpage) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### entries

Individual indexed files and directories, their content and permissions.

- `GET /api/v1/entries`: List entries
- `GET /api/v1/entries/by-hash/{content_hash}`: List entries by content hash
- `GET /api/v1/entries/{entry_id}`: Get an entry
- `GET /api/v1/entries/{entry_id}/content`: Download file content
- `GET /api/v1/entries/{entry_id}/preview`: Preview file content
- `GET /api/v1/entries/{entry_id}/versions`: List entry versions

<a id="op-list-entries"></a>
#### `GET /api/v1/entries`

**List entries** (`list_entries`)

Auth: `userJWT`, scope `files:read`

Non-deleted entries ordered by path, optionally filtered. Entries hidden by per-user ACL filtering are omitted.
Requires scope `files:read`. Source-limited API tokens only see their permitted sources.
Errors: 404 if `source_id` is outside the token's sources; 422 for a malformed `cursor`.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | query | `string` (`uuid`) \| `null` | no | Only entries of this source. |
| `kind` | query | `string` \| `null` | no | Only this kind: `file` or `directory`. |
| `extension` | query | `string` \| `null` | no | Only files with this extension, without the dot. |
| `path_prefix` | query | `string` \| `null` | no | Only entries whose full path starts with this prefix. |
| `cursor` | query | `string` \| `null` | no | Opaque cursor from a previous `next_cursor`. |
| `limit` | query | `integer` | no | Page size. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`Page_EntryResponse_`](#schema-page-entryresponse-) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-entries-by-hash"></a>
#### `GET /api/v1/entries/by-hash/{content_hash}`

**List entries by content hash** (`get_entries_by_hash`)

Auth: `userJWT`, scope `files:read`

Every non-deleted file with this content hash across the visible sources; the unpaginated counterpart of the duplicate group files. Entries hidden by per-user ACL filtering are omitted.
Requires scope `files:read`. Source-limited API tokens only see their permitted sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `content_hash` | path | `string` | yes | Content hash to match. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`EntryResponse`](#schema-entryresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-entry"></a>
#### `GET /api/v1/entries/{entry_id}`

**Get an entry** (`get_entry`)

Auth: `userJWT`, scope `files:read`

Full detail of one entry: metadata, ACL, extended attributes, version history, owning source and tags (direct and inherited).
Requires scope `files:read`. Source-limited API tokens only see their permitted sources.
Errors: 404 if the entry does not exist, is outside the token's sources, or is hidden from the caller by per-user ACL filtering (the same status in every case, so existence is not revealed).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `entry_id` | path | `string` (`uuid`) | yes | Entry id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`EntryDetailResponse`](#schema-entrydetailresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-content"></a>
#### `GET /api/v1/entries/{entry_id}/content`

**Download file content** (`get_content`)

Auth: `userJWT`, scope `files:content`

Streams the file's bytes with the entry's MIME type. Local sources are served directly (range requests supported); other sources are fetched through a scanner process and streamed without a `Content-Length`, because the indexed size may be stale. Use `attachment=true` to force a download.
Requires scope `files:content`. Source-limited API tokens only see their permitted sources.
Errors: 400 for an unsafe path; 403 if the file is not readable by the API; 404 if the entry is not a file, was deleted, its source is gone, it is outside the token's sources or hidden by per-user ACL filtering, or the file is missing on disk.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `entry_id` | path | `string` (`uuid`) | yes | Entry id of a file. |
| `attachment` | query | `boolean` | no | Send `Content-Disposition: attachment` instead of `inline`. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Raw file bytes, served with the entry's MIME type. | `application/octet-stream`: `string` (`binary`) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-preview"></a>
#### `GET /api/v1/entries/{entry_id}/preview`

**Preview file content** (`get_preview`)

Auth: `userJWT`, scope `files:content`

A text preview of the first 64 KiB of the file. Files with a NUL byte in their first 4 KiB are reported as binary with no text; text is decoded as UTF-8, falling back to Latin-1.
Requires scope `files:content`. Source-limited API tokens only see their permitted sources.
Errors: 400 for an unsafe path; 403 if the file is not readable by the API; 404 as for the content endpoint; 502 if the scanner cannot fetch a non-local file.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `entry_id` | path | `string` (`uuid`) | yes | Entry id of a file. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`PreviewResponse`](#schema-previewresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 502 | Bad Gateway | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-entry-versions"></a>
#### `GET /api/v1/entries/{entry_id}/versions`

**List entry versions** (`get_entry_versions`)

Auth: `userJWT`, scope `files:read`

Recorded versions of an entry, newest first. Each captures the content hash, size, mode, ownership, ACL and extended attributes as of the scan that detected it.
Requires scope `files:read`. Source-limited API tokens only see their permitted sources.
Errors: 404 if the entry does not exist, is outside the token's sources, or is hidden from the caller.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `entry_id` | path | `string` (`uuid`) | yes | Entry id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`EntryVersionResponse`](#schema-entryversionresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### effective-permissions

Evaluate which rights a principal holds on an entry, from its stored ACL, mode and ownership.

- `POST /api/v1/entries/{entry_id}/effective-permissions`: Evaluate effective permissions

<a id="op-post-effective-permissions"></a>
#### `POST /api/v1/entries/{entry_id}/effective-permissions`

**Evaluate effective permissions** (`post_effective_permissions`)

Auth: `userJWT`, scope `identities:read`

Computes, for a given principal and its groups, which rights (read, write, execute, delete, change_perms) the entry's stored ACL, mode and ownership grant, with the ACL entries responsible and any caveats of the model used (posix, nfsv4, nt, s3, cloud_drive). It evaluates the stored metadata only; it does not contact the storage system. Despite being a POST it changes nothing.
Requires scope `identities:read`. Source-limited API tokens only see their permitted sources.
Errors: 404 if the entry does not exist or is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `entry_id` | path | `string` (`uuid`) | yes | Entry to evaluate. |

**Request body** (required)
- `application/json`: [`EffectivePermsRequest`](#schema-effectivepermsrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`EffectivePerms`](#schema-effectiveperms) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### browse

Directory-style browsing of indexed sources, with keyset pagination and per-user ACL filtering.

- `GET /api/v1/browse`: Browse a directory
- `GET /api/v1/browse/effective-counts`: Count visible and hidden entries

<a id="op-browse"></a>
#### `GET /api/v1/browse`

**Browse a directory** (`browse`)

Auth: `userJWT`, scope `files:read`

Lists the children of one directory of a source, directories first, in keyset-paginated pages. Entries the caller may not read are omitted when per-user ACL filtering applies (a user with filesystem identity bindings, unless a caller who can bypass ACLs passes `show_all`); a `browse_filtered` audit event is recorded when that hides something.
`total` is only computed on the first page (no `cursor`) and only when `include_total` is true.
Requires scope `files:read`. Source-limited API tokens only see their permitted sources.
Errors: 400 for an invalid `filters` value or a `source` predicate in it (use search for cross-source filters); 404 if the source does not exist or is outside the token's sources; 422 for a malformed `cursor`, or one issued under a different `sort`/`order`.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | query | `string` (`uuid`) | yes | Source to browse. |
| `path` | query | `string` | no | Directory path within the source; `/` is the root. Trailing slashes are ignored. |
| `sort` | query | `"name"` \| `"size"` \| `"modified"` | no | Sort key within directories and within files. |
| `order` | query | `"asc"` \| `"desc"` | no | Sort direction. |
| `show_all` | query | `boolean` | no | For callers allowed to bypass ACL filtering: show entries regardless of filesystem permissions. Ignored for everyone else. |
| `filters` | query | `string` \| `null` | no | Filter predicates, as base64url-encoded JSON, using the same grammar as search (without a `source` predicate). |
| `cursor` | query | `string` \| `null` | no | Opaque cursor from a previous `next_cursor`. Restart pagination if you change `sort` or `order`. |
| `limit` | query | `integer` | no | Page size. |
| `q` | query | `string` \| `null` | no | Case-insensitive substring filter on the entry name. |
| `include_total` | query | `boolean` | no | Compute `total` on the first page. Disable to skip the count on very large folders. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`BrowsePage`](#schema-browsepage) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-effective-counts"></a>
#### `GET /api/v1/browse/effective-counts`

**Count visible and hidden entries** (`effective_counts`)

Auth: `userJWT`, scope `files:read`

For one directory, how many direct children the caller can see and how many per-user ACL filtering hides. Returns `{visible, hidden, enforced}`; `enforced` is false (and `hidden` 0) when no ACL filter applies. Feeds the Browse "N items hidden" footer.
Requires scope `files:read`. Source-limited API tokens only see their permitted sources. Errors: 404 if the source is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | query | `string` (`uuid`) | yes | Source to count in. |
| `path` | query | `string` | no | Directory path within the source; `/` is the root. |
| `show_all` | query | `boolean` | no | For callers allowed to bypass ACL filtering: skip the filter and count everything as visible. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`EffectiveCounts`](#schema-effectivecounts) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### duplicates

Duplicate-file detection and cleanup.

- `GET /api/v1/duplicates`: List duplicate groups
- `POST /api/v1/duplicates/{content_hash}/delete-copies`: Delete duplicate copies
- `GET /api/v1/duplicates/{content_hash}/files`: Files sharing a content hash

<a id="op-list-duplicates"></a>
#### `GET /api/v1/duplicates`

**List duplicate groups** (`list_duplicates`)

Auth: `userJWT`, scope `files:read`

Groups of non-deleted files that share a content hash (two or more copies), largest combined size first. `wasted_bytes` is what keeping a single copy would reclaim. Files hidden by per-user ACL filtering are not counted.
Requires scope `files:read`. Source-limited API tokens only see their permitted sources. Errors: 422 for a malformed `cursor`.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `min_size` | query | `integer` \| `null` | no | Only groups whose single-copy size is at least this many bytes. |
| `cursor` | query | `string` \| `null` | no | Opaque cursor from a previous `next_cursor`. |
| `limit` | query | `integer` | no | Page size. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`Page_DuplicateGroup_`](#schema-page-duplicategroup-) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-delete-duplicate-copies"></a>
#### `POST /api/v1/duplicates/{content_hash}/delete-copies`

**Delete duplicate copies** (`delete_duplicate_copies`)

Auth: `userJWT`, scope `sources:write`

Deletes the listed copies of a duplicate group from the storage system itself and from the index, keeping `keep_entry_id`. Copies are processed one at a time; the response lists which were deleted and which failed (for example a read-only share), so partial success is normal and still returns 200. A failed copy stays indexed. Each outcome is recorded as a `duplicate_copy_deleted` or `duplicate_copy_delete_failed` audit event.
Requires scope `sources:write`. Source-limited API tokens only see their permitted sources.
Errors: 400 if `delete_entry_ids` is empty, contains `keep_entry_id`, or any entry is not a file with this content hash; 404 if any entry does not exist or is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `content_hash` | path | `string` | yes | Content hash of the group the entries must belong to. |

**Request body** (required)
- `application/json`: [`DeleteCopiesRequest`](#schema-deletecopiesrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`DeleteCopiesResponse`](#schema-deletecopiesresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-duplicate-files"></a>
#### `GET /api/v1/duplicates/{content_hash}/files`

**Files sharing a content hash** (`get_duplicate_files`)

Auth: `userJWT`, scope `files:read`

The individual copies in one duplicate group, ordered by entry id. Files hidden by per-user ACL filtering are omitted.
Requires scope `files:read`. Source-limited API tokens only see their permitted sources. Errors: 422 for a malformed `cursor`.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `content_hash` | path | `string` | yes | Content hash shared by the copies, as returned by the group list. |
| `cursor` | query | `string` \| `null` | no | Opaque cursor from a previous `next_cursor`. |
| `limit` | query | `integer` | no | Page size. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`Page_DuplicateFile_`](#schema-page-duplicatefile-) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### tags

User tags on files and sources, with inheritance.

- `GET /api/v1/entries/{entry_id}/tags`: List an entry's tags
- `POST /api/v1/entries/{entry_id}/tags`: Tag an entry
- `DELETE /api/v1/entries/{entry_id}/tags/{tag}`: Remove a tag from an entry
- `GET /api/v1/tags`: List tags
- `POST /api/v1/tags`: Create a tag
- `POST /api/v1/tags/bulk-apply`: Tag many entries
- `GET /api/v1/tags/usage`: Tag usage
- `DELETE /api/v1/tags/{tag_id}`: Delete a tag

<a id="op-get-entry-tags"></a>
#### `GET /api/v1/entries/{entry_id}/tags`

**List an entry's tags** (`get_entry_tags`)

Auth: `userJWT`, scope `tags:read`

The tags on an entry as a list of `{tag, inherited, inherited_from_path}`: direct tags first, then inherited ones, each with the path of the directory it came from. A tag applied both directly and by inheritance appears twice.
Requires scope `tags:read`. Source-limited API tokens only see entries of their permitted sources. Errors: 404 if the entry does not exist or is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `entry_id` | path | `string` (`uuid`) | yes | Entry id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`EntryTagOut`](#schema-entrytagout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-apply-tags-to-entry"></a>
#### `POST /api/v1/entries/{entry_id}/tags`

**Tag an entry** (`apply_tags_to_entry`)

Auth: `userJWT`, scope `tags:write`

Applies one or more tags, by name, directly to an entry; missing tags are added to the catalogue. Tagging a directory makes every descendant inherit the tag. The search index is updated in the background.
Requires scope `tags:write`. A source-limited token may apply only tags that already exist, and only on entries in its sources. Errors: 404 if the entry does not exist or is outside the token's sources; 422 for an empty tag list.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `entry_id` | path | `string` (`uuid`) | yes | Entry to tag. |

**Request body** (required)
- `application/json`: [`_TagApplyBody`](#schema--tagapplybody)

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-remove-tag-from-entry"></a>
#### `DELETE /api/v1/entries/{entry_id}/tags/{tag}`

**Remove a tag from an entry** (`remove_tag_from_entry`)

Auth: `userJWT`, scope `tags:write`

Removes a directly applied tag. Copies inherited from this entry are removed from its descendants, but descendants that were tagged directly keep theirs. The search index is updated in the background.
Requires scope `tags:write`. Errors: 404 if the entry does not exist or is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `entry_id` | path | `string` (`uuid`) | yes | Entry to remove the tag from. |
| `tag` | path | `string` | yes | Tag name. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-tags"></a>
#### `GET /api/v1/tags`

**List tags** (`list_tags`)

Auth: `userJWT`, scope `tags:read`

The tag catalogue ordered by name. A source-limited token sees only tags used on its permitted sources. Not paginated.
Requires scope `tags:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`TagResponse`](#schema-tagresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-create-tag"></a>
#### `POST /api/v1/tags`

**Create a tag** (`create_tag`)

Auth: `userJWT`, scope `tags:write`

Adds a tag to the catalogue with an optional colour. Tags are also created automatically the first time they are applied; this is the explicit path for setting a colour up front.
Requires scope `tags:write`. Not available to source-limited tokens, since the catalogue is global. Errors: 409 if the tag exists; 422 for a malformed body.

**Request body** (required)
- `application/json`: [`TagCreate`](#schema-tagcreate)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`TagResponse`](#schema-tagresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-bulk-apply"></a>
#### `POST /api/v1/tags/bulk-apply`

**Tag many entries** (`bulk_apply`)

Auth: `userJWT`, scope `tags:write`

Applies every listed tag to every listed entry, as for single-entry tagging (directories propagate to descendants). All-or-nothing: if any entry is unknown or outside the caller's sources, nothing is applied. The search index is updated in the background.
Requires scope `tags:write`. A source-limited token may apply only existing tags. Errors: 404 if some entries do not exist or are outside the token's sources; 422 for empty lists.

**Request body** (required)
- `application/json`: [`_BulkApplyBody`](#schema--bulkapplybody)

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-tag-usage"></a>
#### `GET /api/v1/tags/usage`

**Tag usage** (`list_tag_usage`)

Auth: `userJWT`, scope `tags:read`

For each tag, how many entries carry it directly and how many only inherit it from a tagged directory. A source-limited token counts only its permitted sources. Not paginated.
Requires scope `tags:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`TagUsageResponse`](#schema-tagusageresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-delete-tag-catalogue"></a>
#### `DELETE /api/v1/tags/{tag_id}`

**Delete a tag** (`delete_tag_catalogue`)

Auth: `userJWT`, scope `tags:write`

Deletes the tag from the catalogue and removes every direct and inherited application of it, across all sources. Irreversible.
Requires scope `tags:write`. Not available to source-limited tokens, since the effect is global. Errors: 404 if the tag does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `tag_id` | path | `string` (`uuid`) | yes | Tag to delete. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### analytics

Aggregated statistics over the index.

- `GET /api/v1/analytics/extension-trend`: Per-extension trend
- `GET /api/v1/analytics/forecast`: Capacity forecast
- `GET /api/v1/analytics/largest-files`: Largest files
- `GET /api/v1/analytics/owner-distribution`: Owner distribution
- `GET /api/v1/analytics/storage-by-link-role`: Storage by mirror-link role
- `GET /api/v1/analytics/storage-by-source`: Storage by source
- `GET /api/v1/analytics/storage-by-type`: Storage by file type
- `GET /api/v1/analytics/timeseries`: Size or file-count history

<a id="op-get-extension-trend"></a>
#### `GET /api/v1/analytics/extension-trend`

**Per-extension trend** (`get_extension_trend`)

Auth: `userJWT`, scope `analytics:read`

Per-extension series (file count `n` and `bytes`) across a source's snapshots in the window, keyed by extension. A snapshot that lacks an extension contributes zeros so each series stays continuous.
Requires scope `analytics:read`. Errors: 404 if the source is outside the token's sources; 422 for an invalid parameter.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | query | `string` (`uuid`) | yes | Source to chart. |
| `extensions` | query | `string` | yes | Comma-separated extensions, without dots. |
| `days` | query | `integer` | no | Window length in days, ending now. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: map of array of [`ExtensionTrendPoint`](#schema-extensiontrendpoint) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-forecast"></a>
#### `GET /api/v1/analytics/forecast`

**Capacity forecast** (`get_forecast`)

Auth: `userJWT`, scope `analytics:read`

Linear projection of a source's total size from its recent snapshots (least-squares slope with a 95% interval band). With fewer than 3 snapshots in the lookback window `forecast` is null and `reason` is `insufficient_history`.
Requires scope `analytics:read`. Errors: 404 if the source is outside the token's sources; 422 for an invalid parameter.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | query | `string` (`uuid`) | yes | Source to forecast. |
| `horizon_days` | query | `integer` | no | How many days ahead to project. |
| `lookback_days` | query | `integer` | no | How many days of history to fit. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ForecastResponse`](#schema-forecastresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-largest-files"></a>
#### `GET /api/v1/analytics/largest-files`

**Largest files** (`get_largest_files`)

Auth: `userJWT`, scope `analytics:read`

The largest non-deleted files across visible sources, biggest first. Logical-view sources (mirror-link targets) are excluded. Files hidden from the caller by filesystem-ACL filtering are omitted. Requires scope `analytics:read`. Source-limited API tokens only see their permitted sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `n` | query | `integer` | no | Number of files to return (maximum 100). |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`LargestFile`](#schema-largestfile) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-owner-distribution"></a>
#### `GET /api/v1/analytics/owner-distribution`

**Owner distribution** (`get_owner_distribution`)

Auth: `userJWT`, scope `analytics:read`

File count and bytes per owner from the source's latest snapshot, largest first; empty when the source has no snapshot. Owner SIDs are resolved to names from the principals cache where known.
Requires scope `analytics:read`. Errors: 404 if the source is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | query | `string` (`uuid`) | yes | Source to summarise. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`OwnerDistribution`](#schema-ownerdistribution) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-storage-by-link-role"></a>
#### `GET /api/v1/analytics/storage-by-link-role`

**Storage by mirror-link role** (`get_storage_by_link_role`)

Auth: `userJWT`, scope `analytics:read`

File count and bytes of one source's non-deleted files grouped by mirror-link role (`original`, `thumbnail`, `encoded_video`, `sidecar`, `archive`, `version`), with files that are not linked to a mirrored entry in `unlinked`. Always returns all seven buckets in that order, zeros included.
Requires scope `analytics:read`. Errors: 404 if the source is outside the token's sources.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | query | `string` (`uuid`) | yes | Source to break down. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`StorageByLinkRole`](#schema-storagebylinkrole) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-storage-by-source"></a>
#### `GET /api/v1/analytics/storage-by-source`

**Storage by source** (`get_storage_by_source`)

Auth: `userJWT`, scope `analytics:read`

File count and total bytes per visible source over non-deleted files, largest first. Computed live from the index. Sources that are the target of a mirror link (logical views) report `total_size` 0 and `logical_view` true, since their bytes are counted on the backing source. Requires scope `analytics:read`. Source-limited API tokens only see their permitted sources.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`StorageBySource`](#schema-storagebysource) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-storage-by-type"></a>
#### `GET /api/v1/analytics/storage-by-type`

**Storage by file type** (`get_storage_by_type`)

Auth: `userJWT`, scope `analytics:read`

File count and total bytes per extension over the non-deleted files of every visible source, largest first. Computed live from the index. Sources that are the target of a mirror link (logical views, e.g. Immich) are excluded so mirrored bytes count once. Requires scope `analytics:read`. Source-limited API tokens only see their permitted sources.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`StorageByType`](#schema-storagebytype) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-timeseries"></a>
#### `GET /api/v1/analytics/timeseries`

**Size or file-count history** (`get_timeseries`)

Auth: `userJWT`, scope `analytics:read`

History of one source's total size or file count, oldest first, one point per scan snapshot in the window.
Requires scope `analytics:read`. Errors: 404 if the source is outside the token's sources; 422 for an invalid parameter.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | query | `string` (`uuid`) | yes | Source to chart. |
| `metric` | query | `string` | no | `size` plots total bytes, `count` plots file count. |
| `days` | query | `integer` | no | Window length in days, ending now. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`SeriesPoint`](#schema-seriespoint) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### storage

Storage explorer: size breakdowns by tree, type and age.

- `GET /api/v1/storage/children`: Directory children
- `GET /api/v1/storage/sources`: Cross-source treemap
- `GET /api/v1/storage/tree`: Nested subtree

<a id="op-list-children"></a>
#### `GET /api/v1/storage/children`

**Directory children** (`list_children`)

Auth: `userJWT`, scope `analytics:read`

Not paginated: returns the top `limit` children of a directory by size plus an aggregate `other` bucket for the rest, which is what a treemap needs. Each child carries the size and a colour key chosen by `color_by`. When per-user ACL filtering applies, entries hidden from the caller are folded into a synthetic `<hidden>` bucket.
Requires scope `analytics:read`. Source-limited API tokens only see their permitted sources. Errors: 404 if the source is outside the token's sources; 422 for an invalid parameter.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | query | `string` (`uuid`) | yes | Source to look inside. |
| `path` | query | `string` | no | Directory whose immediate children to return. |
| `color_by` | query | `"type"` \| `"age"` \| `"owner"` \| `"risk"` | no | Colour dimension: `type` (extension or directory), `age` (hot under 30 days, warm under a year, else cold), `owner`, or `risk` (marks world- or authenticated-readable entries; only for callers who bypass ACL filtering, others get `type`). |
| `show_all` | query | `boolean` | no | For callers allowed to bypass ACL filtering: include entries regardless of filesystem permissions. Ignored for everyone else. |
| `limit` | query | `integer` | no | Children to return before the remainder is grouped as `other`. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`StorageChildrenResponse`](#schema-storagechildrenresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-storage-sources"></a>
#### `GET /api/v1/storage/sources`

**Cross-source treemap** (`list_storage_sources`)

Auth: `userJWT`, scope `analytics:read`

One row per visible source, sized by the totals of its most recent scan snapshot (zero when never scanned or when the source is a mirror-link logical view), largest first. Not paginated.
Requires scope `analytics:read`. Source-limited API tokens only see their permitted sources.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`StorageSourcesResponse`](#schema-storagesourcesresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-tree"></a>
#### `GET /api/v1/storage/tree`

**Nested subtree** (`get_tree`)

Auth: `userJWT`, scope `analytics:read`

Recursive expansion of a directory into one nested tree sized to a node budget, ready to feed a treemap. `truncated` is true when the budget was reached; each directory then carries an `other` bucket for the size not shown. ACL filtering and the `<hidden>` bucket behave as for the children endpoint.
Requires scope `analytics:read`. Source-limited API tokens only see their permitted sources. Errors: 404 if the source is outside the token's sources; 422 for an invalid parameter.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | query | `string` (`uuid`) | yes | Source to expand. |
| `path` | query | `string` | no | Directory to start from. |
| `max_nodes` | query | `integer` | no | Maximum nodes in the response. |
| `min_bytes` | query | `integer` | no | Skip entries smaller than this many bytes (they count toward `other`). |
| `color_by` | query | `"type"` \| `"age"` \| `"owner"` \| `"risk"` | no | Colour dimension: `type`, `age`, `owner` or `risk` (as for the children endpoint). |
| `show_all` | query | `boolean` | no | For callers allowed to bypass ACL filtering: include entries regardless of filesystem permissions. Ignored for everyone else. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`StorageTreeResponse`](#schema-storagetreeresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### dashboard

Summary data for the landing page.

- `GET /api/v1/dashboard/access-risks`: World-readable file count
- `GET /api/v1/dashboard/summary`: Dashboard summary

<a id="op-get-access-risks"></a>
#### `GET /api/v1/dashboard/access-risks`

**World-readable file count** (`get_access_risks`)

Auth: `userJWT`, scope `analytics:read`

Count of files readable by everyone, cached for a short time (`cache_age_seconds`). Only callers holding `audit:read` and not limited to sources get the figure; any other caller receives 200 with `access_risks: null` so the dashboard tile can render a placeholder without an error.
Requires scope `analytics:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`AccessRisksResponse`](#schema-accessrisksresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-summary"></a>
#### `GET /api/v1/dashboard/summary`

**Dashboard summary** (`get_summary`)

Auth: `userJWT`, scope `analytics:read`

Everything the landing page shows in one call: storage totals with a 30-day delta, scan counters, growth hints for the three largest sources, top owners, fastest-growing extensions, recent scans and identity-mapping counters. User-related counts are only included for callers who can read users and are not source-limited.
Requires scope `analytics:read`. Source-limited API tokens only see their permitted sources.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`DashboardSummary`](#schema-dashboardsummary) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### identities

Your filesystem identities and their per-source bindings (which drive per-user file visibility), unbound identity claims, and group membership resolution.

- `GET /api/v1/identities`: List your identities
- `POST /api/v1/identities`: Create an identity
- `GET /api/v1/identities/unbound`: List unbound identity claims
- `DELETE /api/v1/identities/{person_id}`: Delete an identity
- `PATCH /api/v1/identities/{person_id}`: Update an identity
- `POST /api/v1/identities/{person_id}/bindings`: Bind an identity to a source
- `DELETE /api/v1/identities/{person_id}/bindings/{binding_id}`: Delete a binding
- `PATCH /api/v1/identities/{person_id}/bindings/{binding_id}`: Update a binding
- `POST /api/v1/identities/{person_id}/bindings/{binding_id}/resolve-groups`: Resolve a binding's groups

<a id="op-list-identities"></a>
#### `GET /api/v1/identities`

**List your identities** (`list_identities`)

Auth: `userJWT`, scope `identities:read`

The caller's own filesystem identities (personas), oldest first, each with its per- source bindings. Identities belong to a user; there is no way to list another user's. Requires scope `identities:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`FsPersonOut`](#schema-fspersonout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-create-identity"></a>
#### `POST /api/v1/identities`

**Create an identity** (`create_identity`)

Auth: `userJWT`, scope `identities:write`

Creates a persona for the caller. Records an `identity_added` audit event. Requires scope `identities:write`.

**Request body** (required)
- `application/json`: [`FsPersonIn`](#schema-fspersonin)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`FsPersonOut`](#schema-fspersonout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-list-unbound"></a>
#### `GET /api/v1/identities/unbound`

**List unbound identity claims** (`list_unbound`)

Auth: `userJWT`, scope `identities:read`

OIDC identity claims that could not be bound to any source at login, most recently seen first. By default the caller's own are returned. Callers holding `users:read` may pass `user_id` for another user's, or omit it to see the whole backlog.
Requires scope `identities:read`.
Errors: 403 if a caller without `users:read` passes `user_id` for someone else.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `user_id` | query | `string` (`uuid`) \| `null` | no | Only claims of this user (administrators only for users other than yourself). |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`UnboundIdentityOut`](#schema-unboundidentityout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-delete-identity"></a>
#### `DELETE /api/v1/identities/{person_id}`

**Delete an identity** (`delete_identity`)

Auth: `userJWT`, scope `identities:write`

Deletes the identity and its bindings. Records an `identity_removed` audit event. Requires scope `identities:write`.
Errors: 404 if the identity does not exist or belongs to another user.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `person_id` | path | `string` (`uuid`) | yes | Identity (person) id. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-update-identity"></a>
#### `PATCH /api/v1/identities/{person_id}`

**Update an identity** (`update_identity`)

Auth: `userJWT`, scope `identities:write`

Renames the identity or changes its primary flag. Requires scope `identities:write`.
Errors: 404 if the identity does not exist or belongs to another user.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `person_id` | path | `string` (`uuid`) | yes | Identity (person) id. |

**Request body** (required)
- `application/json`: [`FsPersonPatch`](#schema-fspersonpatch)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`FsPersonOut`](#schema-fspersonout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-create-binding"></a>
#### `POST /api/v1/identities/{person_id}/bindings`

**Bind an identity to a source** (`create_binding`)

Auth: `userJWT`, scope `identities:write`

Declares who the identity is on one source (a POSIX uid, SID, NFSv4 principal or S3 canonical id) and which groups it belongs to. File listings are filtered by the bound principal and group tokens. Records a `binding_added` audit event.
Requires scope `identities:write`.
Errors: 404 if the identity does not exist or belongs to another user; 409 if a binding for this source already exists.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `person_id` | path | `string` (`uuid`) | yes | Identity (person) id. |

**Request body** (required)
- `application/json`: [`FsBindingIn`](#schema-fsbindingin)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`FsBindingOut`](#schema-fsbindingout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-delete-binding"></a>
#### `DELETE /api/v1/identities/{person_id}/bindings/{binding_id}`

**Delete a binding** (`delete_binding`)

Auth: `userJWT`, scope `identities:write`

Removes the binding. Records a `binding_removed` audit event. Requires scope `identities:write`.
Errors: 404 if the identity or binding does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `person_id` | path | `string` (`uuid`) | yes | Identity (person) id. |
| `binding_id` | path | `string` (`uuid`) | yes | Binding id. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-update-binding"></a>
#### `PATCH /api/v1/identities/{person_id}/bindings/{binding_id}`

**Update a binding** (`update_binding`)

Auth: `userJWT`, scope `identities:write`

Changes the binding's identifier, identity type or groups. `groups_source` records where the groups came from and can only be changed by a caller holding `users:write`.
Requires scope `identities:write`.
Errors: 403 if a caller without `users:write` sets `groups_source`; 404 if the identity or binding does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `person_id` | path | `string` (`uuid`) | yes | Identity (person) id. |
| `binding_id` | path | `string` (`uuid`) | yes | Binding id. |

**Request body** (required)
- `application/json`: [`FsBindingPatch`](#schema-fsbindingpatch)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`FsBindingOut`](#schema-fsbindingout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-post-resolve-groups"></a>
#### `POST /api/v1/identities/{person_id}/bindings/{binding_id}/resolve-groups`

**Resolve a binding's groups** (`post_resolve_groups`)

Auth: `userJWT`, scope `identities:write`

Looks up the group memberships of the binding's identity on its source (using the source's own backend), stores them on the binding with `groups_source` set to `auto`, and returns the binding. A cached result younger than the configured group cache lifetime is reused without contacting the backend. Records a `groups_auto_resolved` audit event.
Requires scope `identities:write`. Only your own identities can be used.
Errors: 404 if the identity, binding or its source does not exist; 422 if resolution is unsupported for this source or fails (the detail carries a `reason` and `message`).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `person_id` | path | `string` (`uuid`) | yes | Identity (person) id. |
| `binding_id` | path | `string` (`uuid`) | yes | Binding id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`FsBindingOut`](#schema-fsbindingout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### principals

Principal (SID/UID) lookup and name resolution.

- `POST /api/v1/principals/resolve`: Resolve SIDs to names

<a id="op-resolve"></a>
#### `POST /api/v1/principals/resolve`

**Resolve SIDs to names** (`resolve`)

Auth: `userJWT`, scope `identities:read`

Resolves Windows SIDs to account names through the source's domain controller (LSARPC), with a server-side cache so repeat lookups are free. Used to name principals in an ACL whose names were not available at scan time.
The response maps every requested SID to a result whose `status` is `resolved`, `unresolved`, `skipped` (source type has no LSARPC) or `error` (lookup failed); failures never fail the request.
Requires scope `identities:read`. Not available to source-limited API tokens (403 `source_limited`). Errors: 400 if more than 256 SIDs are sent; 422 for a malformed body.

**Request body** (required)
- `application/json`: [`ResolveRequest`](#schema-resolverequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ResolveResponse`](#schema-resolveresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### access

Admin blast-radius lookups: what a principal can reach, and who can reach a file.

- `GET /api/v1/access`: Look up access (blast radius)

<a id="op-access-query"></a>
#### `GET /api/v1/access`

**Look up access (blast radius)** (`access_query`)

Auth: `userJWT`, scope `access:read`

Answers one of two questions, selected by which parameter is given.
`principal`: what can this principal reach? Returns a summary, a per-source rollup and a paginated sample of files the principal holds `right` on, from the search index.
`file`: who can reach this file? Returns the principals holding `right` on the entry, computed from its stored ACL, mode, uid and gid.
Requires scope `access:read`. Source-limited API tokens only see their permitted sources. Every successful lookup is recorded as an `access_lookup` audit event; failed lookups (4xx) are not.
Errors: 400 if both or neither of `principal` and `file` are given; 404 if the `file` entry does not exist or is outside the token's sources, or if `source_id` names a source outside the token's sources (a `source_id` that does not exist at all simply yields empty results); 422 for a malformed `cursor` or invalid parameter.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `principal` | query | `string` \| `null` | no | Principal token to look up, in the canonical token vocabulary (`sid:S-1-5-...`, `posix:uid:1000`, `posix:gid:100`, `nfsv4:...`, `*` for everyone). Mutually exclusive with `file`. |
| `file` | query | `string` (`uuid`) \| `null` | no | Entry id to list principals for. Mutually exclusive with `principal`. |
| `right` | query | `string` | no | Right to evaluate: `read`, `write` or `delete`. |
| `source_id` | query | `string` (`uuid`) \| `null` | no | Restrict a `principal` lookup to this source. |
| `cursor` | query | `string` \| `null` | no | Opaque cursor from a previous `next_cursor` (principal mode only). |
| `limit` | query | `integer` | no | Page size for the sample of files (principal mode only). |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`PrincipalAccessResponse`](#schema-principalaccessresponse) \| [`FileAccessResponse`](#schema-fileaccessresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### webhooks

Outbound webhook subscriptions.

- `GET /api/v1/webhooks`: List your webhooks
- `POST /api/v1/webhooks`: Create a webhook
- `DELETE /api/v1/webhooks/{webhook_id}`: Delete a webhook

<a id="op-list-webhooks"></a>
#### `GET /api/v1/webhooks`

**List your webhooks** (`list_webhooks`)

Auth: `userJWT`, scope `webhooks:read`

The calling user's webhooks, newest first. Webhooks are private to their owner. The `secret` is never returned.
Requires scope `webhooks:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`WebhookResponse`](#schema-webhookresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-create-webhook"></a>
#### `POST /api/v1/webhooks`

**Create a webhook** (`create_webhook`)

Auth: `userJWT`, scope `webhooks:write`

Registers an outgoing webhook for the calling user. Matching events are POSTed to `url` as JSON (object keys sorted, Python's default `, ` and `: ` separators) with an `X-Akashic-Signature` header: the hex HMAC-SHA256, keyed with `secret`, of the raw request body exactly as received. Verify it over the bytes you received, before parsing. Scan webhooks (`scan.completed`, `scan.failed`) carry `event`, `scan_id` and `source_id`, and fire only for webhooks owned by users whose role holds `scans:write`.
The URL must resolve to a public address; private, loopback and link-local targets are refused, and re-checked at delivery time.
Requires scope `webhooks:write`. Errors: 422 for a malformed body or an unsafe URL.

**Request body** (required)
- `application/json`: [`WebhookCreate`](#schema-webhookcreate)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`WebhookResponse`](#schema-webhookresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-delete-webhook"></a>
#### `DELETE /api/v1/webhooks/{webhook_id}`

**Delete a webhook** (`delete_webhook`)

Auth: `userJWT`, scope `webhooks:write`

Permanently deletes one of your webhooks.
Requires scope `webhooks:write`. Errors: 404 if the webhook does not exist or belongs to another user.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `webhook_id` | path | `string` (`uuid`) | yes | Webhook id. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### purge

Administrative removal of indexed data.

- `POST /api/v1/purge/deleted`: Purge soft-deleted entries
- `POST /api/v1/purge/source/{source_id}`: Purge a source's entries

<a id="op-purge-deleted"></a>
#### `POST /api/v1/purge/deleted`

**Purge soft-deleted entries** (`purge_deleted`)

Auth: `userJWT`, scope `data:purge`

Permanently removes entries that were soft-deleted (no longer seen by scans) more than `older_than_days` days ago, in chunks of 5000, and removes their file documents from the search index. Records a purge log row (`purge_type=deleted`) attributed to the caller. Entries deleted more recently are untouched.
Requires scope `data:purge`, which is destructive and never part of a default role; not available to source-limited tokens. Errors: 422 for `older_than_days` below 1.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `older_than_days` | query | `integer` | no | Only entries soft-deleted at least this many days ago are purged. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`PurgeDeletedResponse`](#schema-purgedeletedresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-purge-source"></a>
#### `POST /api/v1/purge/source/{source_id}`

**Purge a source's entries** (`purge_source`)

Auth: `userJWT`, scope `data:purge`

Irreversibly deletes every indexed entry (files and directories) of one source from the database and, in the background, from the search index. The source itself, its scans and its credentials are kept; a later scan re-indexes it. Records a purge log row (`purge_type=source`) attributed to the caller.
Requires scope `data:purge`, which is destructive and never part of a default role; not available to source-limited tokens. Errors: 404 if the source does not exist; 422 for a malformed id.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `source_id` | path | `string` (`uuid`) | yes | Source whose entries are deleted. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`PurgeSourceResponse`](#schema-purgesourceresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### admin-audit

Administrative audit log.

- `GET /api/v1/admin/audit`: List audit events
- `GET /api/v1/admin/audit/{event_id}`: Get an audit event

<a id="op-list-audit-events"></a>
#### `GET /api/v1/admin/audit`

**List audit events** (`list_audit_events`)

Auth: `userJWT`, scope `audit:read`

Newest first, optionally filtered. `total` is the number of events matching the filters.
Requires scope `audit:read`.
Errors: 422 for a malformed `cursor` or an unparseable `from`/`to` datetime.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `user_id` | query | `string` (`uuid`) \| `null` | no | Only events performed by this user. |
| `event_type` | query | `string` \| `null` | no | Only events of this type, e.g. `access_lookup`, `api_token_created`, `host_created`. |
| `source_id` | query | `string` (`uuid`) \| `null` | no | Only events tied to this source. |
| `from` | query | `string` \| `null` | no | Only events at or after this time (ISO 8601; naive values are taken as UTC). |
| `to` | query | `string` \| `null` | no | Only events at or before this time (ISO 8601; naive values are taken as UTC). |
| `cursor` | query | `string` \| `null` | no | Opaque cursor from a previous `next_cursor`. |
| `limit` | query | `integer` | no | Page size. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`Page_AuditEventOut_`](#schema-page-auditeventout-) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-audit-event"></a>
#### `GET /api/v1/admin/audit/{event_id}`

**Get an audit event** (`get_audit_event`)

Auth: `userJWT`, scope `audit:read`

Requires scope `audit:read`. Errors: 404 if no event has this id.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `event_id` | path | `string` (`uuid`) | yes | Audit event id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`AuditEventOut`](#schema-auditeventout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### maintenance

Long-running maintenance jobs (reindex, rebuild, cleanup).

- `GET /api/v1/admin/maintenance/jobs`: List maintenance jobs
- `POST /api/v1/admin/maintenance/jobs`: Start a maintenance job
- `POST /api/v1/admin/maintenance/logs/purge`: Purge scan logs
- `GET /api/v1/admin/maintenance/overview`: System state overview
- `GET /api/v1/admin/maintenance/scans/stuck`: List active scans
- `POST /api/v1/admin/maintenance/scans/{scan_id}/cancel`: Force-cancel a scan
- `POST /api/v1/admin/maintenance/watchdog/run`: Run the stale-scan watchdog now

<a id="op-list-jobs"></a>
#### `GET /api/v1/admin/maintenance/jobs`

**List maintenance jobs** (`list_jobs`)

Auth: `userJWT`, scope `maintenance:write`

The 20 most recent maintenance jobs, newest first, with status, parameters and result.
Requires scope `maintenance:write`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`JobOut`](#schema-jobout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-start-job"></a>
#### `POST /api/v1/admin/maintenance/jobs`

**Start a maintenance job** (`start_job`)

Auth: `userJWT`, scope `maintenance:write`

Starts a long-running job in the background and returns immediately (202) with a `running` job; poll `GET /admin/maintenance/jobs` for the outcome. Kinds: `reindex_search` (rebuild the search index; optional `batch_size`), `backfill_subtree_sizes` (optional `source_id`), `backfill_viewable` (optional `batch_size`, `source_id`) and `warm_groups` (optional `source_id`). Only one job of a kind runs at a time. The job does not survive an API restart; it is then marked `failed`. Records a `maintenance.job_start` audit event.
Requires scope `maintenance:write`. Errors: 400 for an unknown `kind`; 409 if a job of that kind is already running; 422 for a malformed body.

**Request body** (required)
- `application/json`: [`StartJobRequest`](#schema-startjobrequest)

| Status | Description | Body |
| --- | --- | --- |
| 202 | Successful Response | `application/json`: [`JobOut`](#schema-jobout) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-purge-logs"></a>
#### `POST /api/v1/admin/maintenance/logs/purge`

**Purge scan logs** (`purge_logs`)

Auth: `userJWT`, scope `maintenance:write`

Deletes scan log entries. Give exactly one of `older_than_days` (delete the logs of every finished scan that completed more than that many days ago; `0` deletes all finished scans' logs) or `scan_id` (delete one scan's logs whatever its status). Records a `maintenance.logs_purge` audit event.
Requires scope `maintenance:write`. Errors: 400 unless exactly one of the two fields is given; 422 for a negative number of days.

**Request body** (required)
- `application/json`: [`PurgeLogsRequest`](#schema-purgelogsrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`PurgeLogsOut`](#schema-purgelogsout) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-overview"></a>
#### `GET /api/v1/admin/maintenance/overview`

**System state overview** (`overview`)

Auth: `userJWT`, scope `maintenance:write`

Counts for the Maintenance page: scans by status and how many are active, total entries, sources and scanners, scanners seen within the last 90 seconds, scan log rows and how many the retention sweep (7 days after a scan ends) would delete, and the Meilisearch document count (null if the search service cannot be reached).
Requires scope `maintenance:write`. Not available to source-limited tokens.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`OverviewOut`](#schema-overviewout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-stuck-scans"></a>
#### `GET /api/v1/admin/maintenance/scans/stuck`

**List active scans** (`stuck_scans`)

Auth: `userJWT`, scope `maintenance:write`

Every scan that is still `pending` or `running`, across all sources, oldest first, with the assigned scanner and the age since start. Use it to find scans that stopped making progress; combine with the heartbeat time to judge.
Requires scope `maintenance:write`. Not available to source-limited tokens.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: array of [`StuckScanOut`](#schema-stuckscanout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-cancel-stuck-scan"></a>
#### `POST /api/v1/admin/maintenance/scans/{scan_id}/cancel`

**Force-cancel a scan** (`cancel_stuck_scan`)

Auth: `userJWT`, scope `maintenance:write`

Cancels a `pending` or `running` scan regardless of who started it, marking it `cancelled` with reason `admin` and returning its source to `online`. Idempotent: a scan that already ended is returned unchanged with its current status. The scanner notices on its next heartbeat. Records a `maintenance.scan_cancel` audit event.
Requires scope `maintenance:write`. Errors: 404 if the scan does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan to cancel. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`CancelOut`](#schema-cancelout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-run-watchdog"></a>
#### `POST /api/v1/admin/maintenance/watchdog/run`

**Run the stale-scan watchdog now** (`run_watchdog`)

Auth: `userJWT`, scope `maintenance:write`

Runs the watchdog pass immediately instead of waiting for the scheduler (up to 60 seconds): leases that expired are re-queued, then scans idle past the stale threshold are failed. Returns the number of active scans before and after. Records a `maintenance.watchdog_run` audit event.
Requires scope `maintenance:write`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`WatchdogOut`](#schema-watchdogout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### server-settings

Runtime server configuration.

- `GET /api/v1/server-settings`: List server settings
- `PATCH /api/v1/server-settings`: Change several server settings
- `GET /api/v1/server-settings/{key}`: Get a server setting
- `PATCH /api/v1/server-settings/{key}`: Change a server setting

<a id="op-list-settings"></a>
#### `GET /api/v1/server-settings`

**List server settings** (`list_settings`)

Auth: `userJWT`, scope `settings:read`

Every server setting grouped for display, each with its type, default, current value and whether changing it needs a restart. Secret values are never returned: `value` is a mask when set and empty when not, and `set` says which.
Requires scope `settings:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`SettingsListResponse`](#schema-settingslistresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-patch-settings-bulk"></a>
#### `PATCH /api/v1/server-settings`

**Change several server settings** (`patch_settings_bulk`)

Auth: `userJWT`, scope `settings:write`

Sets many settings in one request. The request is atomic: every value is validated first and nothing is stored if any is invalid. Secret and audit semantics are as for a single change; one `server_setting_updated` event is recorded. Returns every submitted key with its new public value.
Requires scope `settings:write`. Errors: 404 if any key is unknown; 422 if any value is invalid (the error names the key).

**Request body** (required)
- `application/json`: [`BulkPatch`](#schema-bulkpatch)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: map of [`SettingValue`](#schema-settingvalue) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-get-setting-endpoint"></a>
#### `GET /api/v1/server-settings/{key}`

**Get a server setting** (`get_setting_endpoint`)

Auth: `userJWT`, scope `settings:read`

One setting's current value. For a secret, `value` is the mask when set or empty when not, and `set` says which.
Requires scope `settings:read`. Errors: 404 for an unknown key.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `key` | path | `string` | yes | Setting key, as listed by `GET /server-settings`. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`SettingValue`](#schema-settingvalue) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-patch-setting"></a>
#### `PATCH /api/v1/server-settings/{key}`

**Change a server setting** (`patch_setting`)

Auth: `userJWT`, scope `settings:write`

Sets one setting. The value is coerced to the setting's type. For a secret, `null` or the mask leaves it unchanged and an empty string clears it. The change is broadcast to every worker and recorded as a `server_setting_updated` audit event (secret values redacted); settings marked as needing a restart apply after one.
Requires scope `settings:write`. Errors: 404 for an unknown key; 422 if the value is invalid for the setting.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `key` | path | `string` | yes | Setting key, as listed by `GET /server-settings`. |

**Request body** (required)
- `application/json`: [`SettingPatch`](#schema-settingpatch)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`SettingValue`](#schema-settingvalue) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### health

Dependency health and service status.

- `GET /api/v1/health/services`: Check service health
- `GET /api/v1/health/services/activity`: Show service activity

<a id="op-services-health"></a>
#### `GET /api/v1/health/services`

**Check service health** (`services_health`)

Auth: `userJWT`, scope `health:read`

Liveness of Postgres, Redis, Meilisearch and Tika, each probed with a 2 second timeout, with `ok`, `latency_ms` and `error` per service and a `checked_at` timestamp. The result is cached for 5 seconds.
Requires scope `health:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ServicesHealth`](#schema-serviceshealth) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-services-activity"></a>
#### `GET /api/v1/health/services/activity`

**Show service activity** (`services_activity`)

Auth: `userJWT`, scope `health:read`

Tika extraction throughput (queue depth, per-minute counters, last extraction) and Meilisearch activity (document count, pending tasks, last task status). An unreachable backend reports `ok: false` rather than failing the request. Cached for 5 seconds.
Requires scope `health:read`.

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ServicesActivity`](#schema-servicesactivity) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

### Scanner protocol (internal)

Endpoints spoken only by scanner agents; not a public integration surface. Protocol version 2 is required (the handshake answers 426 to anything else). Flow: enrol (`POST /scanners/claim` with a claim token, or `POST /scanners/discover` and wait for approval) -> `POST /scanners/handshake` on every start -> repeat `POST /scans/lease` (and the join long-poll `GET /scanners/{scanner_id}/scans/long-poll`) -> while holding a lease, `POST /scans/{scan_id}/heartbeat`, `POST /ingest/batch` and `/ingest/content`, log posts, and for parallel scans `POST /scans/{scan_id}/work/lease|split|{unit_id}/heartbeat|complete|fail` -> `POST /scans/{scan_id}/complete`. Scanner requests are signed with an Ed25519 JWT (`kid` is the scanner id); ingest, heartbeat and log calls use the `api_jwt` ingest token returned by the lease.

- `POST /api/v1/ingest/batch`: Ingest a batch of scanned entries
- `POST /api/v1/ingest/content`: Ingest extracted file text
- `POST /api/v1/scanners/claim`: Enrol a scanner with a claim token
- `POST /api/v1/scanners/discover`: Request scanner enrolment
- `GET /api/v1/scanners/discover/{discovery_id}`: Poll a discovery request
- `POST /api/v1/scanners/handshake`: Scanner handshake and version check
- `POST /api/v1/scanners/oauth/access-token`: Mint an OAuth access token for a leased source
- `POST /api/v1/scanners/{scanner_id}/heartbeat`: Scanner liveness heartbeat
- `GET /api/v1/scanners/{scanner_id}/probes/long-poll`: Long-poll for a reachability probe
- `POST /api/v1/scanners/{scanner_id}/probes/{request_id}/report`: Report a probe result
- `GET /api/v1/scanners/{scanner_id}/scans/long-poll`: Long-poll for a scan to join
- `POST /api/v1/scans/lease`: Lease a pending scan
- `POST /api/v1/scans/{scan_id}/complete`: Complete a scan lease
- `POST /api/v1/scans/{scan_id}/heartbeat`: Report scan progress
- `POST /api/v1/scans/{scan_id}/log`: Post scan log lines
- `POST /api/v1/scans/{scan_id}/stderr`: Post scanner stderr output
- `POST /api/v1/scans/{scan_id}/work/lease`: Lease a work unit
- `POST /api/v1/scans/{scan_id}/work/split`: Add work units
- `POST /api/v1/scans/{scan_id}/work/{unit_id}/complete`: Complete a work unit
- `POST /api/v1/scans/{scan_id}/work/{unit_id}/fail`: Fail or requeue a work unit
- `POST /api/v1/scans/{scan_id}/work/{unit_id}/heartbeat`: Extend a work unit lease

<a id="op-ingest-batch"></a>
#### `POST /api/v1/ingest/batch`

**Ingest a batch of scanned entries** (`ingest_batch`)

Auth: `ingestJWT`

Scanner protocol. Posts the files and directories a scanner observed, up to 10,000 per request. Entries are upserted by `(source_id, path)`: new paths are created with an initial version snapshot, changed ones are updated and versioned, and entries previously marked deleted are resurrected. Duplicate paths inside one batch keep the last. The first batch moves a `pending` scan to `running`; live counters update the scan's `files_*` totals. The response lists `extract_candidates`, the new or changed files whose text the scanner should extract and send to `POST /ingest/content`.
With `is_final` true on a scan that does not use parallel work units, the scan is completed in the same call: entries the scan did not see are marked deleted (and `moved` events recorded when the same hash reappears elsewhere), the source goes `online` and records `last_scan_at`, and the subtree roll-up, snapshot and `scan.completed` webhooks run in the background. For a scan using work units, finalization happens when the last unit completes instead. A retried final batch on an already completed scan is a no-op.
Authenticated with the short-lived ingest token (`api_jwt`) delivered in the scan lease response, scheme `ingestJWT`; a normal user token or API token is rejected. 401 for a missing, invalid or expired token.
Errors: 400 if `scan_id` belongs to another source; 404 if the scan does not exist (scans are created by `POST /scans/trigger`, never by ingest); 409 if the scan was cancelled or failed; 413 above 10,000 entries; 422 for a malformed body.

**Request body** (required)
- `application/json`: [`ScanBatchIn`](#schema-scanbatchin)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ScanBatchResponse`](#schema-scanbatchresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 413 | Request body too large. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-ingest-content"></a>
#### `POST /api/v1/ingest/content`

**Ingest extracted file text** (`ingest_content`)

Auth: `ingestJWT`

Scanner protocol. Posts text extracted from files (plain text decode or Apache Tika), up to 500 items, keyed by `(source_id, path)`; the text is merged into the search documents so full-text search can match file contents. Paths that do not resolve to an indexed file (content raced ahead of the metadata batch, or the file was deleted) are skipped and picked up by the next scan. Only `content_text` is touched.
Authenticated with the short-lived ingest token (`api_jwt`) delivered in the scan lease response, scheme `ingestJWT`; a normal user token or API token is rejected. 401 for a missing, invalid or expired token.
Errors: 422 for a malformed body or more than 500 items.

**Request body** (required)
- `application/json`: [`ContentBatchIn`](#schema-contentbatchin)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ContentBatchResponse`](#schema-contentbatchresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-claim-with-token"></a>
#### `POST /api/v1/scanners/claim`

**Enrol a scanner with a claim token** (`claim_with_token`)

Auth: None (public)

Scanner protocol, step 0 of the flow `claim -> handshake -> lease -> heartbeat -> complete`. A scanner host holding a one-time claim token (from `POST /scanner-claim-tokens`) posts its own freshly generated Ed25519 public key; the server registers a scanner with the token's pool and restrictions, marks the token used and returns the scanner id. The private key never leaves the scanner. The name is `<token label>-<short id>`. Afterwards sign every request with that key (the `kid` header is the scanner id) and call the handshake. Records a `scanner_claim_token_redeemed` audit event.
No bearer auth: the token is the credential. Rate limited to 5 attempts per minute per client.
Errors: 400 if `public_key_pem` is invalid; 401 for an unknown token; 409 if the key is already registered; 410 if the token was already used or has expired; 422 for a malformed body; 429 when rate limited.

**Request body** (required)
- `application/json`: [`ClaimRequest`](#schema-claimrequest)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`ClaimResponse`](#schema-claimresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 410 | Resource is gone or expired. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 429 | Too many requests. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-discover-scanner"></a>
#### `POST /api/v1/scanners/discover`

**Request scanner enrolment** (`discover_scanner`)

Auth: None (public)

Scanner protocol, enrolment by approval. An unenrolled scanner posts its freshly generated public key and receives a pairing code to show the operator; an admin approves or denies it in the UI (`POST /scanner-discovery-requests/{id}/approve`). Poll `GET /scanners/discover/{discovery_id}` for the outcome. A second request with the same key while one is pending returns the existing request. Requests expire after 15 minutes. Records a `scanner_discovery_requested` audit event.
Public (the approval is the gate), rate limited to 5 requests per minute per client, and disabled by default: it answers 404 unless discovery is enabled in the server settings.
Errors: 400 if `public_key_pem` is not a valid Ed25519 public key; 404 if discovery is disabled; 422 for a malformed body; 429 when rate limited.

**Request body** (required)
- `application/json`: [`DiscoverRequest`](#schema-discoverrequest)

| Status | Description | Body |
| --- | --- | --- |
| 201 | Successful Response | `application/json`: [`DiscoverResponse`](#schema-discoverresponse) |
| 400 | Malformed or unacceptable request. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 429 | Too many requests. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-discover-status"></a>
#### `GET /api/v1/scanners/discover/{discovery_id}`

**Poll a discovery request** (`discover_status`)

Auth: None (public)

Scanner protocol. Long-polls for the outcome of a discovery request: returns at once when it is no longer pending, otherwise waits up to about 25 seconds for the admin's decision and then answers with the current state. `status` is `pending`, `approved` (with `scanner_id`, `name`, `pool` and the server protocol version: continue with the handshake), `denied` (with `deny_reason`) or `expired`. A pending request past its expiry is marked `expired`.
Public: the unguessable `discovery_id` is the credential. Answers 404 while discovery is disabled.
Errors: 404 if discovery is disabled or the request does not exist.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `discovery_id` | path | `string` (`uuid`) | yes | Id returned when the request was created. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`DiscoverStatus`](#schema-discoverstatus) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-handshake"></a>
#### `POST /api/v1/scanners/handshake`

**Scanner handshake and version check** (`handshake`)

Auth: `scannerJWT`

Scanner protocol, step 1 after enrolment and on every agent start. The agent sends its `protocol_version` (this server speaks and accepts protocol version 2 only; the accepted range is returned as `accepted_min` and `accepted_max`). If it is in range the response carries the scanner's effective configuration (Tika URL, extraction workers, concurrent units). Otherwise the server answers 426 with `accepted: false` and a `reason`, and the agent must upgrade before leasing work. The reported version, hostname and last-seen time are recorded even when rejected.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 426 when the protocol version is outside the accepted range (body is a `HandshakeResponse`); 422 for a malformed body.

**Request body** (required)
- `application/json`: [`HandshakeRequest`](#schema-handshakerequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`HandshakeResponse`](#schema-handshakeresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 426 | Protocol version not accepted; the body says which versions are. | `application/json`: [`HandshakeResponse`](#schema-handshakeresponse) |

<a id="op-scanner-mint-oauth-access-token"></a>
#### `POST /api/v1/scanners/oauth/access-token`

**Mint an OAuth access token for a leased source** (`scanner_mint_oauth_access_token`)

Auth: `scannerJWT`

Scanner protocol. Returns a fresh access token for an OAuth-backed source (for example Google Drive) when a long scan outlives the token delivered with the lease. Only a scanner currently holding a running scan or unit lease on the source may ask.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 403 if the scanner has no active lease on the source; 404 if the source has no connected OAuth credential; 502 if the provider refused the refresh; 422 for a malformed body.

**Request body** (required)
- `application/json`: [`OAuthAccessTokenRequest`](#schema-oauthaccesstokenrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`OAuthAccessTokenResponse`](#schema-oauthaccesstokenresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 502 | Bad Gateway | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-scanner-heartbeat"></a>
#### `POST /api/v1/scanners/{scanner_id}/heartbeat`

**Scanner liveness heartbeat** (`scanner_heartbeat`)

Auth: `scannerJWT`

Scanner protocol. Marks the scanner online (it counts as online for 90 seconds) and returns its current effective configuration, so settings changed by an admin reach a running agent. Send roughly every 30 seconds. Separate from the per-scan heartbeat on `POST /scans/{scan_id}/heartbeat`.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 403 if `scanner_id` is not the authenticated scanner.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scanner_id` | path | `string` (`uuid`) | yes | The calling scanner's own id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ScannerEffectiveConfig`](#schema-scannereffectiveconfig) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-long-poll-probes"></a>
#### `GET /api/v1/scanners/{scanner_id}/probes/long-poll`

**Long-poll for a reachability probe** (`long_poll_probes`)

Auth: `scannerJWT`

Scanner protocol. Blocks up to 30 seconds until the server asks this scanner to test whether it can reach a (local) source, then returns the probe with the merged connection config; answer it with `POST /scanners/{scanner_id}/probes/{request_id}/report`. Returns 204 when none arrived; reconnect immediately. Also marks the scanner seen.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 403 if `scanner_id` is not the authenticated scanner.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scanner_id` | path | `string` (`uuid`) | yes | The calling scanner's own id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`ProbeRequest`](#schema-proberequest) \| `null` |
| 204 | No probe within the poll window; reconnect. | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-report-probe-result"></a>
#### `POST /api/v1/scanners/{scanner_id}/probes/{request_id}/report`

**Report a probe result** (`report_probe_result`)

Auth: `scannerJWT`

Scanner protocol. Delivers the outcome of a probe received from the probe long-poll: stored as the scanner's latest reachability for the source and forwarded to whoever requested the test.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 403 if `scanner_id` is not the authenticated scanner; 404 if the source does not exist; 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scanner_id` | path | `string` (`uuid`) | yes | The calling scanner's own id. |
| `request_id` | path | `string` (`uuid`) | yes | Probe request id from the long-poll payload. |

**Request body** (required)
- `application/json`: [`ProbeReport`](#schema-probereport)

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-long-poll-scan-joins"></a>
#### `GET /api/v1/scanners/{scanner_id}/scans/long-poll`

**Long-poll for a scan to join** (`long_poll_scan_joins`)

Auth: `scannerJWT`

Scanner protocol, parallel scans. Blocks up to 30 seconds until a scan on a source with `max_parallel_scanners` above 1 needs more scanners, then returns the same payload as `POST /scans/lease` so the agent can dispatch it identically, then lease units with `POST /scans/{scan_id}/work/lease`. Returns 204 when there is nothing to join; reconnect immediately. Also marks the scanner seen.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 403 if `scanner_id` is not the authenticated scanner.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scanner_id` | path | `string` (`uuid`) | yes | The calling scanner's own id. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`LeasedScan`](#schema-leasedscan) \| `null` |
| 204 | No scan to join within the poll window; reconnect. | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-lease-scan"></a>
#### `POST /api/v1/scans/lease`

**Lease a pending scan** (`lease_scan`)

Auth: `scannerJWT`

Scanner protocol, step 2. Atomically claims one `pending` (or expired-lease) scan whose pool matches the scanner's pool, restricted to the scanner's allowed sources and scan types, oldest first. The lease lasts 60 seconds and is kept alive by the scan heartbeat. Marks the scan `running` and its source `scanning`. The response carries the merged source connection config (secrets included, plus a fresh OAuth `access_token` when the source uses OAuth) and `api_jwt`, a short-lived (24 h) ingest token for `POST /ingest/batch`, `/ingest/content` and the per-scan progress endpoints.
Returns 204 when nothing is pending; poll again after a short wait. Poll the join long-poll `GET /scanners/{scanner_id}/scans/long-poll` as well when the source allows several parallel scanners.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.

**Request body**
- `application/json`: [`LeaseRequest`](#schema-leaserequest) \| `null`

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`LeasedScan`](#schema-leasedscan) \| `null` |
| 204 | No pending work; retry later. | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-complete-scan"></a>
#### `POST /api/v1/scans/{scan_id}/complete`

**Complete a scan lease** (`complete_scan`)

Auth: `scannerJWT`

Scanner protocol, final step. Releases the lease and records the terminal `status` (`completed`, `failed` or `cancelled`) with an optional error message; sets the source back to `online` (or `failed`), updates `last_scan_at` and the scanner's reachability result for a successful scan, and notifies subscribers. Idempotent for the same status. Only the scanner holding the lease may complete it.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 403 if the caller does not hold the lease; 404 if the scan does not exist; 409 if the scan already ended with a different status; 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan whose lease the caller holds. |

**Request body** (required)
- `application/json`: [`CompleteRequest`](#schema-completerequest)

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-post-heartbeat"></a>
#### `POST /api/v1/scans/{scan_id}/heartbeat`

**Report scan progress** (`post_heartbeat`)

Auth: `ingestJWT`

Scanner protocol. Posted about once a second while a scan runs: current path, counters, phase and adaptive batch size. Stores them on the scan, renews the 60 second scan lease and relays progress to live subscribers. A `pending` scan becomes `running`. Doubles as the cancellation channel: once the scan has been cancelled or has ended the call fails with 409 and the scanner must stop.
Authenticated with the short-lived ingest token (`api_jwt`) delivered in the scan lease response, scheme `ingestJWT`; a normal user token or API token is rejected. 401 for a missing, invalid or expired token.
Errors: 404 if the scan does not exist; 409 if the scan is `cancelled`, `completed` or `failed` (the detail carries `status`, `reason` and `message`); 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan being reported on. |

**Request body** (required)
- `application/json`: [`HeartbeatIn`](#schema-heartbeatin)

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-post-log-batch"></a>
#### `POST /api/v1/scans/{scan_id}/log`

**Post scan log lines** (`post_log_batch`)

Auth: `ingestJWT`

Scanner protocol. Stores up to 200 structured log lines (`info`, `warn`, `error`) for the scan, attributed to the leasing scanner, and relays them to live subscribers. An empty list is accepted and ignored. Read back with `GET /scans/{scan_id}/log`.
Authenticated with the short-lived ingest token (`api_jwt`) delivered in the scan lease response, scheme `ingestJWT`; a normal user token or API token is rejected. 401 for a missing, invalid or expired token.
Errors: 404 if the scan does not exist; 422 for a malformed body, more than 200 lines or a message over 8192 characters.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan the lines belong to. |

**Request body** (required)
- `application/json`: [`LogBatchIn`](#schema-logbatchin)

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-post-stderr-batch"></a>
#### `POST /api/v1/scans/{scan_id}/stderr`

**Post scanner stderr output** (`post_stderr_batch`)

Auth: `ingestJWT`

Scanner protocol. Stores up to 200 chunks (4096 characters each) of the scanner process's stderr as log lines of level `stderr`, and relays them to live subscribers. An empty list is accepted and ignored.
Authenticated with the short-lived ingest token (`api_jwt`) delivered in the scan lease response, scheme `ingestJWT`; a normal user token or API token is rejected. 401 for a missing, invalid or expired token.
Errors: 404 if the scan does not exist; 422 for a malformed body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan the output belongs to. |

**Request body** (required)
- `application/json`: [`StderrBatchIn`](#schema-stderrbatchin)

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-lease-unit"></a>
#### `POST /api/v1/scans/{scan_id}/work/lease`

**Lease a work unit** (`lease_unit`)

Auth: `scannerJWT`

Scanner protocol, parallel scans. Atomically claims the next pending work unit (a directory subtree) of the scan for the calling scanner, or an expired lease left by a dead scanner, for 60 seconds. Extend it with the unit heartbeat. The first lease moves a `pending` scan to `running`.
Returns 204 when there is no unit to take (the scanner is done with this scan). The number of distinct scanners working one scan is capped by the source's `max_parallel_scanners`.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 404 if the scan does not exist; 409 if the scan has ended (detail `reason: scan-terminal`) or the parallel-scanner cap is reached while work remains.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan whose work queue to lease from. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`WorkUnitOut`](#schema-workunitout) \| `null` |
| 204 | No work unit available right now; keep polling. | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-split-units"></a>
#### `POST /api/v1/scans/{scan_id}/work/split`

**Add work units** (`split_units`)

Auth: `scannerJWT`

Scanner protocol, parallel scans. Adds pending units for subdirectories the scanner chooses not to walk itself, so other scanners can lease them; the first scanner on a fresh scan also uses it to enqueue the root unit (`parent_unit_id` null, `child_paths` `[""]`). Idempotent per path: paths that already exist are counted in `skipped`, so retrying is safe. Scanners that may join this scan are notified.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 404 if the scan does not exist; 409 if the scan has ended.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan to add units to. |

**Request body** (required)
- `application/json`: [`SplitRequest`](#schema-splitrequest)

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`SplitResponse`](#schema-splitresponse) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-complete-unit"></a>
#### `POST /api/v1/scans/{scan_id}/work/{unit_id}/complete`

**Complete a work unit** (`complete_unit`)

Auth: `scannerJWT`

Scanner protocol, parallel scans. Marks a leased unit completed. When it was the last unfinished unit the scan itself is finalized in the same transaction: stale entries are swept, the source returns to `online` and records `last_scan_at`, subscribers are notified, and the subtree roll-up, snapshot and `scan.completed` webhooks run in the background. A scan with failed units finishes `completed` if any unit succeeded and `failed` otherwise.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 403 if the caller does not hold the lease; 404 if the scan or unit does not exist; 409 if the scan has ended or the unit is not running.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan the unit belongs to. |
| `unit_id` | path | `string` (`uuid`) | yes | Leased work unit. |

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-fail-unit"></a>
#### `POST /api/v1/scans/{scan_id}/work/{unit_id}/fail`

**Fail or requeue a work unit** (`fail_unit`)

Auth: `scannerJWT`

Scanner protocol, parallel scans. Reports that a leased unit could not be finished. With `requeue` true the unit returns to the queue for another scanner, up to 3 attempts in total; otherwise (or after the third attempt) it becomes `failed`, which may finalize the scan as described for unit completion.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 403 if the caller does not hold the lease; 404 if the scan or unit does not exist; 409 if the scan has ended or the unit is not running.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan the unit belongs to. |
| `unit_id` | path | `string` (`uuid`) | yes | Leased work unit. |

**Request body** (required)
- `application/json`: [`CompleteUnitRequest`](#schema-completeunitrequest)

| Status | Description | Body |
| --- | --- | --- |
| 204 | Successful Response | none |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

<a id="op-heartbeat-unit"></a>
#### `POST /api/v1/scans/{scan_id}/work/{unit_id}/heartbeat`

**Extend a work unit lease** (`heartbeat_unit`)

Auth: `scannerJWT`

Scanner protocol, parallel scans. Renews the 60 second lease on a unit the scanner holds. Send it more often than every 60 seconds while walking the unit.
Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.
Errors: 403 if the caller does not hold the lease; 404 if the unit does not exist in the scan; 409 if the unit has already finished.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `scan_id` | path | `string` (`uuid`) | yes | Scan the unit belongs to. |
| `unit_id` | path | `string` (`uuid`) | yes | Leased work unit. |

| Status | Description | Body |
| --- | --- | --- |
| 200 | Successful Response | `application/json`: [`WorkUnitOut`](#schema-workunitout) |
| 401 | Missing, invalid or expired credentials. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 403 | Authenticated but not permitted. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 404 | Resource not found. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 409 | Conflicts with the current resource state. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |
| 422 | Request failed validation. | `application/json`: [`ErrorResponse`](#schema-errorresponse) |

## WebSockets

Browsers cannot set headers on the handshake, so authenticate with `?token=<jwt>`, a login access token. Personal API tokens (`akp_...`) are not accepted on WebSockets: the socket is closed with code 1008. Auth failures close the socket with code 1008 before any frame is sent. Every frame is a JSON object with a `kind` discriminator; the server sends a `ping` frame every 30 seconds and an `error` frame when its live feed breaks (fall back to polling and reconnect). On every connect the first frame is a `snapshot`.

### `/ws/v1/scans/{scan_id}`

Live progress and log lines of one scan. Requires `scans:read`; a source-limited token sees only its sources.

| Event | `kind` | Description |
| --- | --- | --- |
| [`WsScanSnapshot`](#schema-wsscansnapshot) | `snapshot` | First frame on `/ws/v1/scans/{id}`: current scan state plus recent log lines. |
| [`WsScanProgress`](#schema-wsscanprogress) | `progress` | Throttled progress frame on `/ws/v1/scans/{id}`. |
| [`WsScanLog`](#schema-wsscanlog) | `log` / `stderr` | New log (or stderr) lines on `/ws/v1/scans/{id}`. |
| [`WsPing`](#schema-wsping) | `ping` | Server keep-alive frame. |
| [`WsError`](#schema-wserror) | `error` | Live stream failed; clients should fall back to polling. |

### `/ws/v1/scans`

Scan and source state changes for every source the caller can see. Requires `scans:read`.

| Event | `kind` | Description |
| --- | --- | --- |
| [`WsScanListSnapshot`](#schema-wsscanlistsnapshot) | `snapshot` | First frame on `/ws/v1/scans`: the pending, running and failed scans the caller can see. |
| [`WsScanState`](#schema-wsscanstate) | `scan.state` | Scan state change on `/ws/v1/scans` (the source-list stream). |
| [`WsSourceCreated`](#schema-wssourcecreated) | `source.created` | A source was created (`/ws/v1/scans`). |
| [`WsSourceUpdated`](#schema-wssourceupdated) | `source.updated` | A source was edited; refetch it (`/ws/v1/scans`). |
| [`WsSourceDeleted`](#schema-wssourcedeleted) | `source.deleted` | A source was deleted (`/ws/v1/scans`). |
| [`WsHostChanged`](#schema-wshostchanged) | `host.changed` | A host was created, edited or deleted; refetch hosts (`/ws/v1/scans`). |
| [`WsPing`](#schema-wsping) | `ping` | Server keep-alive frame. |
| [`WsError`](#schema-wserror) | `error` | Live stream failed; clients should fall back to polling. |

### `/ws/v1/scanners`

Scanner lifecycle events: enrolment, discovery requests and scanner changes. Requires `scanners:read`.

| Event | `kind` | Description |
| --- | --- | --- |
| [`WsScannersSnapshot`](#schema-wsscannerssnapshot) | `snapshot` | First frame on `/ws/v1/scanners`: pending discovery requests. |
| [`WsScannerClaimRedeemed`](#schema-wsscannerclaimredeemed) | `scanner.claim_redeemed` | A claim token was redeemed and a scanner registered (`/ws/v1/scanners`). |
| [`WsScannerDiscoveryRequested`](#schema-wsscannerdiscoveryrequested) | `scanner.discovery_requested` | A scanner asked to be approved (`/ws/v1/scanners`). |
| [`WsScannerDiscoveryApproved`](#schema-wsscannerdiscoveryapproved) | `scanner.discovery_approved` | A discovery request was approved (`/ws/v1/scanners`). |
| [`WsScannerDiscoveryDenied`](#schema-wsscannerdiscoverydenied) | `scanner.discovery_denied` | A discovery request was denied (`/ws/v1/scanners`). |
| [`WsScannerDiscoveryExpired`](#schema-wsscannerdiscoveryexpired) | `scanner.discovery_expired` | A discovery request expired unanswered (`/ws/v1/scanners`). |
| [`WsScannerUpdated`](#schema-wsscannerupdated) | `scanner.updated` | A scanner's settings or reported metadata changed; refetch it (`/ws/v1/scanners`). |
| [`WsScannerDeleted`](#schema-wsscannerdeleted) | `scanner.deleted` | A scanner was removed (`/ws/v1/scanners`). |
| [`WsPing`](#schema-wsping) | `ping` | Server keep-alive frame. |
| [`WsError`](#schema-wserror) | `error` | Live stream failed; clients should fall back to polling. |

## Schemas

<a id="schema-acereference"></a>
### ACEReference

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ace_index` | `integer` | yes | Position of the ACL entry that decided the result; -1 for a synthetic reason such as the base mode bits. |
| `summary` | `string` | yes | One-line, human-readable description of that entry. |

<a id="schema-acl-input"></a>
### ACL-Input

Type: [`PosixACL-Input`](#schema-posixacl-input) \| [`NfsV4ACL-Input`](#schema-nfsv4acl-input) \| [`NtACL-Input`](#schema-ntacl-input) \| [`S3ACL-Input`](#schema-s3acl-input) \| [`CloudDriveACL-Input`](#schema-clouddriveacl-input)
Discriminator: `type`

<a id="schema-acl-output"></a>
### ACL-Output

Type: [`PosixACL-Output`](#schema-posixacl-output) \| [`NfsV4ACL-Output`](#schema-nfsv4acl-output) \| [`NtACL-Output`](#schema-ntacl-output) \| [`S3ACL-Output`](#schema-s3acl-output) \| [`CloudDriveACL-Output`](#schema-clouddriveacl-output)
Discriminator: `type`

<a id="schema-accessprincipal"></a>
### AccessPrincipal

The principal that was looked up.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `domain` | `string` \| `null` | yes | Resolved domain, when known. |
| `kind` | `string` | yes | `user`, `group`, `wildcard` or `unknown`. |
| `name` | `string` \| `null` | yes | Resolved name, when known. |
| `token` | `string` | yes | Canonical principal token, e.g. `sid:S-1-5-...` or `posix:uid:1000`. |

<a id="schema-accessrisks"></a>
### AccessRisks

World-readable exposure counters.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `public_read_count` | `integer` | yes | Files readable by everyone. |

<a id="schema-accessrisksresponse"></a>
### AccessRisksResponse

Cached world-readable file count.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `access_risks` | [`AccessRisks`](#schema-accessrisks) \| `null` | yes | Null when the caller may not see it. |
| `cache_age_seconds` | `integer` \| `null` | yes | Age of the cached value; absent when not visible. |

<a id="schema-accesssamplehit"></a>
### AccessSampleHit

One reachable file from the search index.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `filename` | `string` | yes | Base name. |
| `fs_modified_at` | `integer` \| `null` | yes | Modified time, epoch seconds. |
| `id` | `string` | yes | Entry id. |
| `owner_name` | `string` \| `null` | yes | Owner name. |
| `path` | `string` | yes | Full path. |
| `size_bytes` | `integer` \| `null` | yes | Size in bytes. |
| `source_id` | `string` | yes | Source id. |

<a id="schema-accesssourcerollup"></a>
### AccessSourceRollup

Reachable files within one source.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `file_count` | `integer` | yes | Reachable files in the source. |
| `source_id` | `string` | yes | Source id. |
| `source_name` | `string` | yes | Source display name. |

<a id="schema-accesssummary"></a>
### AccessSummary

Totals across everything the principal can reach.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `file_count` | `integer` | yes | Files reachable. |
| `source_count` | `integer` | yes | Sources containing reachable files. |
| `total_size_bytes` | `integer` | yes | Total bytes reachable. |

<a id="schema-addsharesitem"></a>
### AddSharesItem

One row in an /add-shares batch.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes | Name for the new source; must be unique across all sources. |
| `share` | `string` | yes | Share name (SMB), export path (NFS) or bucket (S3). |

<a id="schema-addsharesrequest"></a>
### AddSharesRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `exclude_patterns` | array of `string` \| `null` | no | Path patterns to exclude from scans of every created source. |
| `is_removable` | `boolean` \| `null` | no | Whether the created sources are removable or external storage; null infers it from the host type. |
| `max_parallel_scanners` | `integer` \| `null` | no | Scanner cap (1 to 16) for every created source; null inherits from the host. |
| `preferred_pool` | `string` \| `null` | no | Restrict every created source to this scanner pool; null lets any scanner claim it. |
| `scan_schedule` | `string` \| `null` | no | Cron expression applied to every created source; null for no schedule. |
| `shares` | array of [`AddSharesItem`](#schema-addsharesitem) | yes | Shares to create as sources. |

<a id="schema-addsharesresponse"></a>
### AddSharesResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created` | `integer` | yes | Number of sources created. |
| `skipped` | `integer` | yes | Number of entries skipped because the host already has a source for that share, or the source name is taken. |
| `sources` | array of `string` (`uuid`) | yes | Ids of the sources that were created. |

<a id="schema-adminapitokenresponse"></a>
### AdminApiTokenResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` (`date-time`) | yes | When the token was created. |
| `expires_at` | `string` (`date-time`) \| `null` | yes | When the token expires; null means never. |
| `id` | `string` (`uuid`) | yes | Token id. |
| `last_used_at` | `string` (`date-time`) \| `null` | yes | Last use (refreshed at most once a minute). |
| `last_used_ip` | `string` \| `null` | yes | Client address of the last use. |
| `name` | `string` | yes | Label. |
| `revoked_at` | `string` (`date-time`) \| `null` | yes | When the token was revoked; null while active. |
| `scopes` | array of `string` | yes | Scopes granted by the token (a ceiling: the owner's role also applies). |
| `source_ids` | array of `string` (`uuid`) \| `null` | yes | Sources the token is limited to; null means every source. |
| `token_prefix` | `string` | yes | First characters of the token, to recognise it by. |
| `user_id` | `string` (`uuid`) | yes | Owner's user id. |
| `username` | `string` | yes | Owner's username. |

<a id="schema-allowedscannersrequest"></a>
### AllowedScannersRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `scanner_ids` | array of `string` (`uuid`) | yes | Scanners that should be allowed to scan the source; all others are excluded. |

<a id="schema-allowedscannersresponse"></a>
### AllowedScannersResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `updated_scanners` | `integer` | yes | Scanners whose allow-list changed. |

<a id="schema-apitokencreate"></a>
### ApiTokenCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `expires_at` | `string` (`date-time`) \| `null` | no | When the token stops working. Omit for no expiry. |
| `name` | `string` | yes | A label to recognise the token by. |
| `scopes` | array of `string` | yes | Scopes the token may use. Must be within your own role's scopes. |
| `source_ids` | array of `string` (`uuid`) \| `null` | no | Limit the token to these sources. Omit for every source. |

<a id="schema-apitokencreated"></a>
### ApiTokenCreated

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` (`date-time`) | yes | When the token was created. |
| `expires_at` | `string` (`date-time`) \| `null` | yes | When the token expires; null means never. |
| `id` | `string` (`uuid`) | yes | Token id. |
| `last_used_at` | `string` (`date-time`) \| `null` | yes | Last use (refreshed at most once a minute). |
| `last_used_ip` | `string` \| `null` | yes | Client address of the last use. |
| `name` | `string` | yes | Label. |
| `revoked_at` | `string` (`date-time`) \| `null` | yes | When the token was revoked; null while active. |
| `scopes` | array of `string` | yes | Scopes granted by the token (a ceiling: the owner's role also applies). |
| `source_ids` | array of `string` (`uuid`) \| `null` | yes | Sources the token is limited to; null means every source. |
| `token` | `string` | yes | The token itself. Shown once and never retrievable again. |
| `token_prefix` | `string` | yes | First characters of the token, to recognise it by. |

<a id="schema-apitokenresponse"></a>
### ApiTokenResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` (`date-time`) | yes | When the token was created. |
| `expires_at` | `string` (`date-time`) \| `null` | yes | When the token expires; null means never. |
| `id` | `string` (`uuid`) | yes | Token id. |
| `last_used_at` | `string` (`date-time`) \| `null` | yes | Last use (refreshed at most once a minute). |
| `last_used_ip` | `string` \| `null` | yes | Client address of the last use. |
| `name` | `string` | yes | Label. |
| `revoked_at` | `string` (`date-time`) \| `null` | yes | When the token was revoked; null while active. |
| `scopes` | array of `string` | yes | Scopes granted by the token (a ceiling: the owner's role also applies). |
| `source_ids` | array of `string` (`uuid`) \| `null` | yes | Sources the token is limited to; null means every source. |
| `token_prefix` | `string` | yes | First characters of the token, to recognise it by. |

<a id="schema-apitokenupdate"></a>
### ApiTokenUpdate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` \| `null` | no | New label. |
| `scopes` | array of `string` \| `null` | no | A subset of the token's current scopes. Scopes can only be narrowed. |

<a id="schema-approvebody"></a>
### ApproveBody

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes | Name for the new scanner; must be unique. |
| `pool` | `string` | no | Pool the scanner joins. |

<a id="schema-auditeventout"></a>
### AuditEventOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `event_type` | `string` | yes | Event type, e.g. `access_lookup`, `api_token_created`, `host_created`. |
| `id` | `string` (`uuid`) | yes | Event id. |
| `occurred_at` | `string` (`date-time`) | yes | When the event happened. |
| `payload` | `object` | yes | Event-specific details; the shape depends on `event_type`. Free-form: the shape depends on the event type. |
| `request_ip` | `string` | yes | Client address of the request; empty for system events. |
| `source_id` | `string` (`uuid`) \| `null` | yes | Source the event relates to, if any. |
| `user_agent` | `string` | yes | Client user agent; empty when not available. |
| `user_id` | `string` (`uuid`) \| `null` | yes | User who performed the action; null when the event is not attributable to a user. |

<a id="schema-backingfileref"></a>
### BackingFileRef

An on-disk backing file linked to an app entry.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` (`uuid`) | yes | Backing file entry id. |
| `link_role` | `string` | yes | Role of the backing file. |
| `path` | `string` | yes | Full path of the backing file within its source. |
| `size_bytes` | `integer` \| `null` | yes | Size in bytes; null when unknown. |
| `source_id` | `string` (`uuid`) | yes | Source holding the backing file. |
| `source_name` | `string` | yes | Name of that source. |

<a id="schema-browseentry"></a>
### BrowseEntry

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `child_count` | `integer` \| `null` | yes | For directories, the number of non-deleted direct children; null for files. |
| `content_hash` | `string` \| `null` | yes | Content hash. Files with identical content share it; null until computed. |
| `extension` | `string` \| `null` | yes | File extension without the dot; null for directories and files without one. |
| `fs_modified_at` | `string` (`date-time`) \| `null` | yes | Modification time on the storage system. |
| `group_name` | `string` \| `null` | yes | Group name or SID as reported by the storage system. |
| `id` | `string` (`uuid`) | yes | Entry id. |
| `kind` | `string` | yes | `file` or `directory`. |
| `link_role` | `string` \| `null` | yes | For a backing file linked to an Immich/Paperless entry: its role; null when not linked. |
| `linked_entry_id` | `string` (`uuid`) \| `null` | yes | Id of the app entry this file is linked to; null when not linked. |
| `linked_entry_name` | `string` \| `null` | yes | Name of the linked app entry; null when not linked or not accessible to the caller. |
| `mime_type` | `string` \| `null` | yes | Detected MIME type; null when not detected. |
| `mode` | `integer` \| `null` | yes | POSIX mode bits (file type and permissions) as an integer; null where the connector reports none. |
| `name` | `string` | yes | Base name. |
| `owner_name` | `string` \| `null` | yes | Owner name or SID as reported by the storage system. |
| `path` | `string` | yes | Full path within the source, starting with `/`. |
| `size_bytes` | `integer` \| `null` | yes | Size in bytes; null when unknown. |

<a id="schema-browsepage"></a>
### BrowsePage

One page of a directory listing.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `is_root` | `boolean` | yes | Whether `path` is the source root. |
| `items` | array of [`BrowseEntry`](#schema-browseentry) | yes | The page of results. |
| `limit` | `integer` | yes | Maximum number of items requested for this page. |
| `next_cursor` | `string` \| `null` | yes | Opaque cursor for the next page; null when this is the last page. |
| `parent_path` | `string` \| `null` | yes | Parent directory path; null at the root. |
| `path` | `string` | yes | Normalised directory path. |
| `source_id` | `string` (`uuid`) | yes | Source being browsed. |
| `source_name` | `string` | yes | Source display name. |
| `total` | `integer` \| `null` | yes | Total matching items, when the endpoint computes it (see the endpoint description); otherwise null. |

<a id="schema-bulkpatch"></a>
### BulkPatch

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `values` | `object` | yes | New values keyed by setting key. |

<a id="schema-cancelout"></a>
### CancelOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `scan_id` | `string` (`uuid`) | yes | Scan id. |
| `status` | `string` | yes | Scan status after the call (`cancelled`, or the terminal status it already had). |

<a id="schema-cancelresponse"></a>
### CancelResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `scan_id` | `string` (`uuid`) | yes | Scan id. |
| `status` | `string` | yes | Scan status after the call (`cancelled`, or the status it already had). |

<a id="schema-claimrequest"></a>
### ClaimRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `agent_version` | `string` \| `null` | no | Agent build version. |
| `hostname` | `string` \| `null` | no | RFC 1123 host name of the scanner host. |
| `public_key_pem` | `string` | yes | The scanner's freshly generated Ed25519 public key, PEM encoded. The private key stays on the scanner. |
| `token` | `string` | yes | One-time claim token from `POST /scanner-claim-tokens`. |

<a id="schema-claimresponse"></a>
### ClaimResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes | Generated scanner name. |
| `pool` | `string` | yes | Pool the scanner belongs to. |
| `scanner_id` | `string` (`uuid`) | yes | Id of the new scanner; use it as the JWT `kid` and `sub`. |
| `server_protocol_version` | `integer` | yes | Protocol version this server speaks. |

<a id="schema-claimsnippets"></a>
### ClaimSnippets

Install commands with the claim token filled in.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `compose` | `string` | yes | Docker Compose service. |
| `docker_run` | `string` | yes | `docker run` command. |
| `env` | `string` | yes | Environment file. |
| `k8s` | `string` | yes | Kubernetes manifest. |
| `shell` | `string` | yes | Shell one-liner. |

<a id="schema-claimtokencreate"></a>
### ClaimTokenCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `allowed_scan_types` | array of `string` \| `null` | no | Restrict enrolled scanners to these scan types (`incremental`, `full`); null for any. |
| `allowed_source_ids` | array of `string` (`uuid`) \| `null` | no | Restrict enrolled scanners to these sources; null for any. |
| `label` | `string` | yes | Name for the token, used to name scanners enrolled with it. |
| `pool` | `string` | no | Pool scanners enrolled with the token join. |
| `ttl_minutes` | `integer` | no | Minutes until the token expires (1 to 43200, 30 days). |

<a id="schema-claimtokencreated"></a>
### ClaimTokenCreated

Returned ONCE on POST /api/v1/scanner-claim-tokens. The plaintext
`token` field isn't persisted on the api side (we keep only its
sha256 hash) — copy it now or revoke and regenerate.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `allowed_scan_types` | array of `string` \| `null` | yes | Scan type restriction for enrolled scanners. |
| `allowed_source_ids` | array of `string` (`uuid`) \| `null` | yes | Source restriction for enrolled scanners. |
| `expires_at` | `string` (`date-time`) | yes | When the token expires. |
| `id` | `string` (`uuid`) | yes | Token id. |
| `label` | `string` | yes | Token label. |
| `pool` | `string` | yes | Pool for enrolled scanners. |
| `snippets` | [`ClaimSnippets`](#schema-claimsnippets) | yes | Ready-to-paste install commands, with the token filled in. |
| `token` | `string` | yes | The plaintext claim token, shown only in this response. |

<a id="schema-claimtokensummary"></a>
### ClaimTokenSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `allowed_scan_types` | array of `string` \| `null` | yes | Scan type restriction for enrolled scanners. |
| `allowed_source_ids` | array of `string` (`uuid`) \| `null` | yes | Source restriction for enrolled scanners. |
| `created_at` | `string` (`date-time`) | yes | When the token was created. |
| `expires_at` | `string` (`date-time`) | yes | When the token expires (or expired, or was revoked). |
| `id` | `string` (`uuid`) | yes | Token id. |
| `label` | `string` | yes | Token label. |
| `pool` | `string` | yes | Pool for enrolled scanners. |
| `status` | `string` | yes | `active`, `used` or `expired`. |
| `used_at` | `string` (`date-time`) \| `null` | yes | When the token was redeemed. |
| `used_by_scanner_id` | `string` (`uuid`) \| `null` | yes | Scanner created by redeeming it. |

<a id="schema-clouddriveacl-input"></a>
### CloudDriveACL-Input

Cloud-drive ACL — fifth discriminator alongside posix/nfsv4/nt/s3.

Fundamentally a list of per-principal grants rather than a POSIX-style
owner+mode+ACE. Inheritance is per-grant rather than tree-wide, and
"anyone with link" is a first-class principal type.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `domain_restricted_to` | `string` \| `null` | no | Domain that sharing is restricted to, when the provider enforces such a boundary; null otherwise. |
| `grants` | array of [`CloudDriveGrant-Input`](#schema-clouddrivegrant-input) | no | Per-principal grants on the entry. |
| `type` | `"cloud_drive"` | yes | Discriminator, always `cloud_drive`. |

<a id="schema-clouddriveacl-output"></a>
### CloudDriveACL-Output

Cloud-drive ACL — fifth discriminator alongside posix/nfsv4/nt/s3.

Fundamentally a list of per-principal grants rather than a POSIX-style
owner+mode+ACE. Inheritance is per-grant rather than tree-wide, and
"anyone with link" is a first-class principal type.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `domain_restricted_to` | `string` \| `null` | yes | Domain that sharing is restricted to, when the provider enforces such a boundary; null otherwise. |
| `grants` | array of [`CloudDriveGrant-Output`](#schema-clouddrivegrant-output) | yes | Per-principal grants on the entry. |
| `type` | `"cloud_drive"` | yes | Discriminator, always `cloud_drive`. |

<a id="schema-clouddrivegrant-input"></a>
### CloudDriveGrant-Input

One ``(principal, role)`` grant. ``inherited`` flags whether the
grant comes from an ancestor folder rather than this entry directly;
``inherited_from`` lets the UI render "inherited from /Foo/Bar" the
way the existing tag system does.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `inherited` | `boolean` | no | True when the grant comes from an ancestor folder. |
| `inherited_from_id` | `string` \| `null` | no | Provider id of the ancestor the grant is inherited from. |
| `inherited_from_path` | `string` \| `null` | no | Path of the ancestor the grant is inherited from. |
| `link` | [`CloudDriveLink`](#schema-clouddrivelink) \| `null` | no | The shared link the grant rides on, for link-based grants; null otherwise. |
| `principal` | [`CloudDrivePrincipal-Input`](#schema-clouddriveprincipal-input) | yes | Who the grant is for. |
| `role` | `"owner"` \| `"writer"` \| `"commenter"` \| `"reader"` \| `"file_organizer"` | yes | `owner`, `writer`, `commenter`, `reader` or `file_organizer`. |

<a id="schema-clouddrivegrant-output"></a>
### CloudDriveGrant-Output

One ``(principal, role)`` grant. ``inherited`` flags whether the
grant comes from an ancestor folder rather than this entry directly;
``inherited_from`` lets the UI render "inherited from /Foo/Bar" the
way the existing tag system does.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `inherited` | `boolean` | yes | True when the grant comes from an ancestor folder. |
| `inherited_from_id` | `string` \| `null` | yes | Provider id of the ancestor the grant is inherited from. |
| `inherited_from_path` | `string` \| `null` | yes | Path of the ancestor the grant is inherited from. |
| `link` | [`CloudDriveLink`](#schema-clouddrivelink) \| `null` | yes | The shared link the grant rides on, for link-based grants; null otherwise. |
| `principal` | [`CloudDrivePrincipal-Output`](#schema-clouddriveprincipal-output) | yes | Who the grant is for. |
| `role` | `"owner"` \| `"writer"` \| `"commenter"` \| `"reader"` \| `"file_organizer"` | yes | `owner`, `writer`, `commenter`, `reader` or `file_organizer`. |

<a id="schema-clouddrivelink"></a>
### CloudDriveLink

The shareable link an "anyone-with-link" grant rides on. Populated
only when the provider exposes link metadata (Drive does; Dropbox
surfaces it as a separate sharing/list_shared_links result we fold in
here). ``scope`` mirrors Drive semantics: anyone unrestricted,
domain-only, or restricted (named principals only).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes | Provider's identifier for the shared link. |
| `scope` | `"anyone"` \| `"domain"` \| `"restricted"` | yes | Who can use the link: `anyone`, `domain` or `restricted` (named principals only). |

<a id="schema-clouddriveprincipal-input"></a>
### CloudDrivePrincipal-Input

One principal in a cloud-drive grant. ``id`` is whatever the provider
uses to identify that principal (Drive permissionId, Microsoft Graph
id, Dropbox account_id) — the connector keeps the raw value so we
can call back into the provider when needed.

``email`` and ``name`` are present when the provider returns them with
the grant, so the UI doesn't need a per-grant lookup. Anonymous
"anyone with the link" grants come through as ``type='anyone'`` with
no email/name set.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` \| `null` | no | Email address, when the provider returns one. |
| `id` | `string` | yes | Provider's identifier for the principal (Drive permission id, Microsoft Graph id or Dropbox account id). |
| `name` | `string` \| `null` | no | Display name, when the provider returns one. |
| `type` | `"user"` \| `"group"` \| `"anyone"` \| `"domain"` | yes | `user`, `group`, `anyone` (public link) or `domain`. |

<a id="schema-clouddriveprincipal-output"></a>
### CloudDrivePrincipal-Output

One principal in a cloud-drive grant. ``id`` is whatever the provider
uses to identify that principal (Drive permissionId, Microsoft Graph
id, Dropbox account_id) — the connector keeps the raw value so we
can call back into the provider when needed.

``email`` and ``name`` are present when the provider returns them with
the grant, so the UI doesn't need a per-grant lookup. Anonymous
"anyone with the link" grants come through as ``type='anyone'`` with
no email/name set.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` \| `null` | yes | Email address, when the provider returns one. |
| `id` | `string` | yes | Provider's identifier for the principal (Drive permission id, Microsoft Graph id or Dropbox account id). |
| `name` | `string` \| `null` | yes | Display name, when the provider returns one. |
| `type` | `"user"` \| `"group"` \| `"anyone"` \| `"domain"` | yes | `user`, `group`, `anyone` (public link) or `domain`. |

<a id="schema-completerequest"></a>
### CompleteRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `error_message` | `string` \| `null` | no | Failure detail, recorded on the scan. |
| `status` | `string` | yes | Terminal status: `completed`, `failed` or `cancelled`. |

<a id="schema-completeunitrequest"></a>
### CompleteUnitRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `error_message` | `string` \| `null` | no | Why the unit failed. |
| `requeue` | `boolean` | no | Return the unit to the queue for another attempt (up to 3 attempts) instead of failing it. |

<a id="schema-contentbatchin"></a>
### ContentBatchIn

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `items` | array of [`ContentItemIn`](#schema-contentitemin) | yes | Up to 500 files. |
| `scan_id` | `string` (`uuid`) | yes | Scan that produced the text. |
| `source_id` | `string` (`uuid`) | yes | Source the files belong to. |

<a id="schema-contentbatchresponse"></a>
### ContentBatchResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `items_indexed` | `integer` | yes | Items matched to an indexed file and written to the search index. |
| `scan_id` | `string` (`uuid`) | yes | Scan the batch belonged to. |

<a id="schema-contentitemin"></a>
### ContentItemIn

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `content_text` | `string` | no | Extracted text. |
| `path` | `string` | yes | Path of the file, as in the metadata batch. |

<a id="schema-credentialprofilecreate"></a>
### CredentialProfileCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `credentials` | `object` | no | Credential values for the type, e.g. `username` and `password` for SMB. Stored encrypted. Free-form: keys depend on the profile type. |
| `description` | `string` \| `null` | no | Free-text note. |
| `name` | `string` | yes | Unique profile name. |
| `type` | `string` | yes | Credential type: `smb`, `nfs` or `s3`. |

<a id="schema-credentialprofileresponse"></a>
### CredentialProfileResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` (`date-time`) | yes | When the profile was created. |
| `credentials` | `object` | yes | Credential values with secrets masked as `***`. Free-form: keys depend on the profile type. |
| `description` | `string` \| `null` | yes | Free-text note. |
| `id` | `string` (`uuid`) | yes | Profile id. |
| `name` | `string` | yes | Profile name. |
| `type` | `"smb"` \| `"nfs"` \| `"s3"` | yes | Credential type: `smb`, `nfs` or `s3`. |
| `updated_at` | `string` (`date-time`) | yes | When the profile was last changed. |

<a id="schema-credentialprofilesummary"></a>
### CredentialProfileSummary

Lean shape for the picker dropdown — name + type only.

No credentials surface here so a list endpoint can be served
without a per-row scrub pass.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `description` | `string` \| `null` | yes | Free-text note. |
| `id` | `string` (`uuid`) | yes | Profile id. |
| `name` | `string` | yes | Profile name. |
| `type` | `"smb"` \| `"nfs"` \| `"s3"` | yes | Credential type: `smb`, `nfs` or `s3`. |

<a id="schema-credentialprofileupdate"></a>
### CredentialProfileUpdate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `credentials` | `object` \| `null` | no | Credential values to change. A value of `***` keeps the stored value; keys not present are left as they are. Free-form: keys depend on the profile type. |
| `description` | `string` \| `null` | no | New note. |
| `name` | `string` \| `null` | no | New unique name. |

<a id="schema-dashboardscans"></a>
### DashboardScans

Source and scan counters.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `active` | `integer` | yes | Sources currently scanning. |
| `total_sources` | `integer` | yes | Sources visible to the caller. |

<a id="schema-dashboardstorage"></a>
### DashboardStorage

Storage totals across visible sources.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `delta_30d_bytes` | `integer` \| `null` | yes | Byte change over 30 days; null without a 30-day-old snapshot. |
| `delta_30d_files` | `integer` \| `null` | yes | File-count change over 30 days; null without history. |
| `total_bytes` | `integer` | yes | Total bytes in the latest snapshots. |
| `total_files` | `integer` | yes | Total files in the latest snapshots. |

<a id="schema-dashboardsummary"></a>
### DashboardSummary

Everything the dashboard tiles need in one call.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `access_risks` | [`AccessRisks`](#schema-accessrisks) \| `null` | yes | Always null here; use `/dashboard/access-risks`. |
| `forecast_hints` | array of [`ForecastHint`](#schema-forecasthint) | yes | Growth hints for the three largest sources. |
| `identity_health` | [`IdentityHealth`](#schema-identityhealth) | yes | Identity-mapping counters. |
| `recent_scans` | array of [`RecentScan`](#schema-recentscan) | yes | Most recent scans. |
| `scans` | [`DashboardScans`](#schema-dashboardscans) | yes | Scan counters. |
| `storage` | [`DashboardStorage`](#schema-dashboardstorage) | yes | Storage totals. |
| `top_extensions_growth_30d` | array of [`ExtensionGrowth`](#schema-extensiongrowth) | yes | Fastest-growing extensions. |
| `top_owners` | array of [`OwnerShare`](#schema-ownershare) | yes | Largest owners across sources. |

<a id="schema-deletecopiesrequest"></a>
### DeleteCopiesRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `delete_entry_ids` | array of `string` (`uuid`) | yes | Entries to delete. Must be non-empty, must not include `keep_entry_id`, and every entry must be a file with the group's content hash. |
| `keep_entry_id` | `string` (`uuid`) | yes | Entry to keep. It must belong to the duplicate group. |

<a id="schema-deletecopiesresponse"></a>
### DeleteCopiesResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `deleted` | array of [`DeleteCopyOutcome`](#schema-deletecopyoutcome) | yes | Copies deleted from storage and the index. |
| `failed` | array of [`DeleteCopyOutcome`](#schema-deletecopyoutcome) | yes | Copies that could not be deleted; they remain indexed. |

<a id="schema-deletecopyoutcome"></a>
### DeleteCopyOutcome

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `entry_id` | `string` | yes | Entry the outcome is for. |
| `message` | `string` | yes | Failure message, for a failed copy; empty on success. |
| `ok` | `boolean` | yes | Whether the copy was deleted. |
| `path` | `string` | yes | Path of the entry. |
| `step` | `string` | yes | Step that failed, for a failed copy; empty on success. |

<a id="schema-denybody"></a>
### DenyBody

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `reason` | `string` \| `null` | no | Reason shown to the scanner. |

<a id="schema-discoverrequest"></a>
### DiscoverRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `agent_version` | `string` \| `null` | no | Agent build version. |
| `hostname` | `string` \| `null` | no | Scanner host name, shown to the approving admin. |
| `public_key_pem` | `string` | yes | The scanner's freshly generated Ed25519 public key, PEM encoded. |
| `requested_pool` | `string` \| `null` | no | Pool the scanner asks to join; the admin decides the actual pool. |

<a id="schema-discoverresponse"></a>
### DiscoverResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `discovery_id` | `string` (`uuid`) | yes | Id to poll for the outcome. |
| `expires_at` | `string` (`date-time`) | yes | When the request lapses if undecided. |
| `pairing_code` | `string` | yes | Short code (`ABCD-EFGH`) to display so the admin can match the request to this host. |
| `poll_after_seconds` | `integer` | yes | Suggested delay before polling the status endpoint. |

<a id="schema-discoverstatus"></a>
### DiscoverStatus

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `deny_reason` | `string` \| `null` | yes | When denied: the admin's reason, if given. |
| `expires_at` | `string` (`date-time`) \| `null` | yes | While pending: when the request lapses. |
| `name` | `string` \| `null` | yes | When approved: scanner name. |
| `pool` | `string` \| `null` | yes | When approved: scanner pool. |
| `scanner_id` | `string` (`uuid`) \| `null` | yes | When approved: id of the registered scanner (the `kid` for signing JWTs). |
| `server_protocol_version` | `integer` \| `null` | yes | When approved: the protocol version the server speaks. |
| `status` | `string` | yes | `pending`, `approved`, `denied` or `expired`. |

<a id="schema-discoveryadminsummary"></a>
### DiscoveryAdminSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `agent_version` | `string` \| `null` | yes | Agent build version. |
| `approved_scanner_id` | `string` (`uuid`) \| `null` | yes | Scanner created on approval. |
| `decided_at` | `string` (`date-time`) \| `null` | yes | When an admin approved or denied it. |
| `deny_reason` | `string` \| `null` | yes | Reason given on denial. |
| `expires_at` | `string` (`date-time`) | yes | When the request lapses if undecided. |
| `hostname` | `string` \| `null` | yes | Scanner host name. |
| `id` | `string` (`uuid`) | yes | Discovery request id. |
| `key_fingerprint` | `string` | yes | Fingerprint of the scanner's public key; compare it with the one shown on the host. |
| `pairing_code` | `string` | yes | Pairing code the scanner displays. |
| `requested_at` | `string` (`date-time`) | yes | When the request was made. |
| `requested_pool` | `string` \| `null` | yes | Pool the scanner asked for. |
| `status` | `string` | yes | `pending`, `approved`, `denied` or `expired`. |

<a id="schema-discoveryapproved"></a>
### DiscoveryApproved

The scanner created for an approved discovery request.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes | Scanner name. |
| `pool` | `string` | yes | Scanner pool. |
| `scanner_id` | `string` (`uuid`) | yes | Id of the newly registered scanner. |

<a id="schema-duplicatefile"></a>
### DuplicateFile

One copy within a duplicate group.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `content_hash` | `string` \| `null` | yes | Content hash. |
| `extension` | `string` \| `null` | yes | Extension without the dot. |
| `filename` | `string` | yes | Base name. |
| `first_seen_at` | `string` | yes | First time a scan saw the entry, ISO 8601. |
| `fs_modified_at` | `string` \| `null` | yes | Filesystem modified time, ISO 8601. |
| `id` | `string` (`uuid`) | yes | Entry id. |
| `is_deleted` | `boolean` | yes | Whether the entry is soft-deleted. |
| `last_seen_at` | `string` | yes | Most recent scan sighting, ISO 8601. |
| `mime_type` | `string` \| `null` | yes | Detected MIME type. |
| `path` | `string` | yes | Full path. |
| `size_bytes` | `integer` \| `null` | yes | Size in bytes. |
| `source_id` | `string` (`uuid`) | yes | Owning source id. |

<a id="schema-duplicategroup"></a>
### DuplicateGroup

A set of files sharing one content hash.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `content_hash` | `string` | yes | Shared content hash. |
| `count` | `integer` | yes | Number of copies. |
| `file_size` | `integer` | yes | Size of a single copy, in bytes. |
| `total_size` | `integer` | yes | Combined size of all copies, in bytes. |
| `wasted_bytes` | `integer` | yes | Bytes reclaimable by keeping one copy. |

<a id="schema-effectivecounts"></a>
### EffectiveCounts

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `enforced` | `boolean` | yes | Whether ACL filtering applied to this request. |
| `hidden` | `integer` | yes | Direct children hidden by per-user ACL filtering; 0 when no filter applies. |
| `visible` | `integer` | yes | Direct children of the directory the caller can see. |

<a id="schema-effectiveperms"></a>
### EffectivePerms

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `evaluated_with` | [`EffectivePermsEvaluatedWith`](#schema-effectivepermsevaluatedwith) | yes | What the evaluation used. |
| `rights` | map of [`RightResult`](#schema-rightresult) | yes | Result per right: `read`, `write`, `execute`, `delete`, `change_perms`. |

<a id="schema-effectivepermsevaluatedwith"></a>
### EffectivePermsEvaluatedWith

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `caveats` | array of `string` | yes | Limits of the evaluation that may make the result differ from what the storage system enforces. |
| `groups` | array of [`GroupRef-Output`](#schema-groupref-output) | yes | The groups taken into account. |
| `model` | `"posix"` \| `"nfsv4"` \| `"nt"` \| `"s3"` \| `"cloud_drive"` \| `"none"` | yes | Permission model used: `posix`, `nfsv4`, `nt`, `s3`, `cloud_drive`, or `none` when the entry has no usable permissions. |
| `principal` | [`PrincipalRef-Output`](#schema-principalref-output) | yes | The principal that was evaluated. |

<a id="schema-effectivepermsrequest"></a>
### EffectivePermsRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `groups` | array of [`GroupRef-Input`](#schema-groupref-input) | no | Groups the principal belongs to; they are not looked up for you. |
| `principal` | [`PrincipalRef-Input`](#schema-principalref-input) | yes | The principal to evaluate. |
| `principal_name_hint` | `string` | no | Optional display name for the principal, used only to improve messages. |

<a id="schema-entrydetailresponse"></a>
### EntryDetailResponse

Full entry detail; includes ACL, xattrs, version history.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `acl` | [`ACL-Output`](#schema-acl-output) \| `null` | yes | Access control list as scanned, in the model of the source (`posix`, `nfsv4`, `nt`, `s3` or `cloud_drive`); null when none. |
| `backing_files` | array of [`BackingFileRef`](#schema-backingfileref) | yes | For an app entry, the backing files linked to it that the caller can access, ordered by role then path. |
| `backing_total_bytes` | `integer` \| `null` | yes | Total size of the listed backing files; null when there are none. |
| `content_hash` | `string` \| `null` | yes | Content hash. Files with identical content share it; null until computed. |
| `domain_metadata` | `object` \| `null` | yes | Provider-specific metadata for self-hosted libraries (Paperless-ngx, Immich); null for filesystem sources. Free-form: keys depend on the provider. |
| `extension` | `string` \| `null` | yes | File extension without the dot; null for directories and files without one. |
| `first_seen_at` | `string` (`date-time`) | yes | When a scan first saw the entry. |
| `fs_accessed_at` | `string` (`date-time`) \| `null` | yes | Last access time on the storage system. |
| `fs_created_at` | `string` (`date-time`) \| `null` | yes | Creation time on the storage system. |
| `fs_modified_at` | `string` (`date-time`) \| `null` | yes | Modification time on the storage system. |
| `gid` | `integer` \| `null` | yes | Numeric group id on POSIX storage; null elsewhere. |
| `group_name` | `string` \| `null` | yes | Group name or SID as reported by the storage system. |
| `id` | `string` (`uuid`) | yes | Entry id. |
| `is_deleted` | `boolean` | yes | True once a scan no longer finds the entry (soft delete). |
| `kind` | `string` | yes | `file` or `directory`. |
| `last_seen_at` | `string` (`date-time`) | yes | When a scan most recently saw the entry. |
| `link_role` | `string` \| `null` | yes | For a backing file linked to an Immich/Paperless entry: its role (`original`, `thumbnail`, `encoded_video`, `sidecar`, `archive` or `version`); null when not linked. |
| `linked_entry` | [`LinkedEntryRef`](#schema-linkedentryref) \| `null` | yes | For a backing file, the app entry it is linked to; null when not linked or when the caller cannot access that entry. |
| `linked_entry_id` | `string` (`uuid`) \| `null` | yes | Id of the Immich/Paperless entry this backing file is linked to; null when not linked. |
| `mime_type` | `string` \| `null` | yes | Detected MIME type; null when not detected. |
| `mode` | `integer` \| `null` | yes | POSIX mode bits (file type and permissions) as an integer; null where the connector reports none. |
| `name` | `string` | yes | Base name. |
| `native_id` | `string` \| `null` | yes | Provider identifier for cloud-drive entries (Drive, OneDrive, Dropbox); null otherwise. |
| `owner_name` | `string` \| `null` | yes | Owner name or SID as reported by the storage system. |
| `parent_path` | `string` | yes | Path of the containing directory; `/` for top-level entries. |
| `path` | `string` | yes | Full path within the source, starting with `/`. |
| `size_bytes` | `integer` \| `null` | yes | Size in bytes; null when unknown. |
| `source` | [`_EntrySourceRef`](#schema--entrysourceref) \| `null` | yes | The owning source; null for an orphaned entry. |
| `source_id` | `string` (`uuid`) \| `null` | yes | Owning source id; null for an orphaned entry whose source was deleted with its entries preserved. |
| `tags` | array of [`EntryTagAssignment`](#schema-entrytagassignment) | yes | Tags on the entry, direct and inherited. |
| `uid` | `integer` \| `null` | yes | Numeric owner id on POSIX storage; null elsewhere. |
| `versions` | array of [`EntryVersionResponse`](#schema-entryversionresponse) | yes | Recorded versions of the entry, newest first. |
| `xattrs` | map of `string` \| `null` | yes | Extended attributes as name/value pairs; null when none. |

<a id="schema-entryin"></a>
### EntryIn

Inbound from the scanner; one row per file/directory observed in a scan.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `acl` | [`ACL-Input`](#schema-acl-input) \| `null` | no | Access control list as read from the storage system; null when it reports none. |
| `content_hash` | `string` \| `null` | no | Content hash. Files with identical content share it; null until computed. |
| `domain_metadata` | `object` \| `null` | no | Provider-specific metadata for document libraries (such as Paperless or Immich); null for filesystems. Free-form: keys depend on the provider. |
| `extension` | `string` \| `null` | no | File extension without the dot; null for directories and files without one. |
| `fs_accessed_at` | `string` (`date-time`) \| `null` | no | Last access time on the storage system. |
| `fs_created_at` | `string` (`date-time`) \| `null` | no | Creation time on the storage system. |
| `fs_modified_at` | `string` (`date-time`) \| `null` | no | Modification time on the storage system. |
| `gid` | `integer` \| `null` | no | Numeric group id on POSIX storage; null elsewhere. |
| `group_name` | `string` \| `null` | no | Group name or SID as reported by the storage system. |
| `kind` | `"file"` \| `"directory"` | no | `file` or `directory`. |
| `mime_type` | `string` \| `null` | no | Detected MIME type; null when not detected. |
| `mode` | `integer` \| `null` | no | POSIX mode bits (file type and permissions) as an integer; null where the connector reports none. |
| `name` | `string` | yes | Base name. |
| `native_id` | `string` \| `null` | no | Provider's own identifier for cloud-drive entries; null for filesystems. |
| `owner_name` | `string` \| `null` | no | Owner name or SID as reported by the storage system. |
| `path` | `string` | yes | Full path within the source, starting with `/`. |
| `size_bytes` | `integer` \| `null` | no | Size in bytes; null when unknown. |
| `subtree_dir_count` | `integer` \| `null` | no | Directories only: directories below it, when computed by the connector. |
| `subtree_file_count` | `integer` \| `null` | no | Directories only: files below it, when computed by the connector. |
| `subtree_size_bytes` | `integer` \| `null` | no | Directories only: total bytes below it, when the connector computes this; otherwise filled in after the scan. |
| `uid` | `integer` \| `null` | no | Numeric owner id on POSIX storage; null elsewhere. |
| `xattrs` | map of `string` \| `null` | no | Extended attributes as name to value strings. |

<a id="schema-entryresponse"></a>
### EntryResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `content_hash` | `string` \| `null` | yes | Content hash. Files with identical content share it; null until computed. |
| `extension` | `string` \| `null` | yes | File extension without the dot; null for directories and files without one. |
| `first_seen_at` | `string` (`date-time`) | yes | When a scan first saw the entry. |
| `fs_modified_at` | `string` (`date-time`) \| `null` | yes | Modification time on the storage system. |
| `gid` | `integer` \| `null` | yes | Numeric group id on POSIX storage; null elsewhere. |
| `group_name` | `string` \| `null` | yes | Group name or SID as reported by the storage system. |
| `id` | `string` (`uuid`) | yes | Entry id. |
| `is_deleted` | `boolean` | yes | True once a scan no longer finds the entry (soft delete). |
| `kind` | `string` | yes | `file` or `directory`. |
| `last_seen_at` | `string` (`date-time`) | yes | When a scan most recently saw the entry. |
| `link_role` | `string` \| `null` | yes | For a backing file linked to an Immich/Paperless entry: its role (`original`, `thumbnail`, `encoded_video`, `sidecar`, `archive` or `version`); null when not linked. |
| `linked_entry_id` | `string` (`uuid`) \| `null` | yes | Id of the Immich/Paperless entry this backing file is linked to; null when not linked. |
| `mime_type` | `string` \| `null` | yes | Detected MIME type; null when not detected. |
| `mode` | `integer` \| `null` | yes | POSIX mode bits (file type and permissions) as an integer; null where the connector reports none. |
| `name` | `string` | yes | Base name. |
| `owner_name` | `string` \| `null` | yes | Owner name or SID as reported by the storage system. |
| `parent_path` | `string` | yes | Path of the containing directory; `/` for top-level entries. |
| `path` | `string` | yes | Full path within the source, starting with `/`. |
| `size_bytes` | `integer` \| `null` | yes | Size in bytes; null when unknown. |
| `source_id` | `string` (`uuid`) \| `null` | yes | Owning source id; null for an orphaned entry whose source was deleted with its entries preserved. |
| `uid` | `integer` \| `null` | yes | Numeric owner id on POSIX storage; null elsewhere. |

<a id="schema-entrytagassignment"></a>
### EntryTagAssignment

One tag on an entry, with origin metadata. Direct tags (the user
applied this tag here) carry `inherited=False, inherited_from_path=None`;
inherited tags carry `inherited=True` and the ancestor directory's
path so the UI can render "Inherited from /Reports" tooltips and let
the user navigate to the source.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `inherited` | `boolean` | yes | True when the tag comes from an ancestor directory rather than being applied to this entry. |
| `inherited_from_path` | `string` \| `null` | yes | Path of the ancestor directory the tag is inherited from; null for a direct tag. |
| `tag` | `string` | yes | Tag name. |

<a id="schema-entrytagout"></a>
### EntryTagOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `inherited` | `boolean` | yes | True when the tag comes from a tagged ancestor directory rather than being applied to this entry. |
| `inherited_from_path` | `string` \| `null` | yes | Path of the ancestor directory the tag is inherited from; null for a direct tag. |
| `tag` | `string` | yes | Tag name. |

<a id="schema-entryversionresponse"></a>
### EntryVersionResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `acl` | [`ACL-Output`](#schema-acl-output) \| `null` | yes | ACL at the time of this version; null when none was recorded. |
| `content_hash` | `string` \| `null` | yes | Content hash. Files with identical content share it; null until computed. |
| `detected_at` | `string` (`date-time`) | yes | When a scan detected this version. |
| `entry_id` | `string` (`uuid`) | yes | Entry this version belongs to. |
| `gid` | `integer` \| `null` | yes | Numeric group id on POSIX storage; null elsewhere. |
| `group_name` | `string` \| `null` | yes | Group name or SID as reported by the storage system. |
| `id` | `string` (`uuid`) | yes | Version id. |
| `mode` | `integer` \| `null` | yes | POSIX mode bits (file type and permissions) as an integer; null where the connector reports none. |
| `owner_name` | `string` \| `null` | yes | Owner name or SID as reported by the storage system. |
| `scan_id` | `string` (`uuid`) \| `null` | yes | Scan that detected this version; null if that scan has since been removed. |
| `size_bytes` | `integer` \| `null` | yes | Size in bytes; null when unknown. |
| `uid` | `integer` \| `null` | yes | Numeric owner id on POSIX storage; null elsewhere. |
| `xattrs` | map of `string` \| `null` | yes | Extended attributes at the time of this version. |

<a id="schema-errorresponse"></a>
### ErrorResponse

Body of every non-2xx response.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `code` | `string` | yes | Stable machine-readable error code, e.g. `not_found` or `validation_error`. |
| `detail` | any \| `null` | yes | Optional structured extra. For `validation_error` this is the list of field errors (`loc`, `msg`, `type`); other errors may carry an object. |
| `message` | `string` | yes | Human-readable summary of what went wrong. |
| `request_id` | `string` | yes | Correlation id, also returned in the `X-Request-ID` header. |

<a id="schema-extensiongrowth"></a>
### ExtensionGrowth

An extension's growth over 30 days.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `current_bytes` | `integer` | yes | Current bytes. |
| `delta_bytes` | `integer` | yes | Bytes gained over 30 days. |
| `extension` | `string` | yes | Extension without the dot. |

<a id="schema-extensiontrendpoint"></a>
### ExtensionTrendPoint

One snapshot's totals for an extension.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `bytes` | `integer` | yes | Total bytes of files with this extension. |
| `n` | `integer` | yes | File count with this extension. |
| `taken_at` | `string` | yes | Snapshot time, ISO 8601. |

<a id="schema-extractcandidate"></a>
### ExtractCandidate

A new-or-changed file the scanner should extract text from.
v0.30.0 — extraction moved into the scanner; the API tells it
which files actually changed so it doesn't re-extract everything
every scan.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `mime_type` | `string` | yes | Detected MIME type; empty if unknown. |
| `path` | `string` | yes | File path to read and extract text from. |
| `size_bytes` | `integer` | yes | File size in bytes. |

<a id="schema-fileaccessresponse"></a>
### FileAccessResponse

File-to-principals lookup (`?file=`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `entry_id` | `string` (`uuid`) | yes | Entry id. |
| `filename` | `string` | yes | Base name. |
| `path` | `string` | yes | Full path. |
| `principals` | array of [`FilePrincipal`](#schema-fileprincipal) | yes | Principals that hold the right. |
| `right` | `string` | yes | `read`, `write` or `delete`. |

<a id="schema-fileprincipal"></a>
### FilePrincipal

A principal holding the right on a file.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `domain` | `string` \| `null` | yes | Resolved domain (SIDs only). |
| `kind` | `string` | yes | `user`, `group`, `wildcard` or `unknown`. |
| `name` | `string` \| `null` | yes | Resolved name (SIDs only). |
| `source` | `string` | yes | Where the grant comes from (`acl`). |
| `token` | `string` | yes | Canonical principal token. |

<a id="schema-forecasthint"></a>
### ForecastHint

Recent growth rate for one of the largest sources.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `current_bytes` | `integer` | yes | Current total bytes. |
| `slope_bytes_per_day` | `integer` | yes | Growth in bytes per day over the last 30 days. |
| `source_id` | `string` (`uuid`) | yes | Source id. |
| `source_name` | `string` \| `null` | yes | Source display name. |

<a id="schema-forecastpoint"></a>
### ForecastPoint

One projected point with its 95% interval.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `high` | `integer` | yes | Upper bound of the 95% interval. |
| `low` | `integer` | yes | Lower bound of the 95% interval. |
| `taken_at` | `string` | yes | Projected time, ISO 8601. |
| `value` | `integer` | yes | Projected size in bytes. |

<a id="schema-forecastprojection"></a>
### ForecastProjection

The linear projection computed from history.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `horizon_days` | `integer` | yes | Days projected beyond the last snapshot. |
| `points` | array of [`ForecastPoint`](#schema-forecastpoint) | yes | Projected points, oldest to newest. |
| `slope_bytes_per_day` | `integer` | yes | Fitted growth rate in bytes per day. |

<a id="schema-forecastresponse"></a>
### ForecastResponse

Capacity forecast for one source.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `forecast` | [`ForecastProjection`](#schema-forecastprojection) \| `null` | yes | Projection; null when there is too little history. |
| `history` | array of [`SeriesPoint`](#schema-seriespoint) | yes | Observed total size per snapshot, oldest to newest. |
| `reason` | `string` | yes | `ok`, or `insufficient_history` when fewer than 3 snapshots exist. |

<a id="schema-fsbindingin"></a>
### FsBindingIn

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `groups` | array of `string` | no | Identifiers of the groups the principal belongs to. Blank entries are dropped. |
| `identifier` | `string` | yes | The identifier itself (a uid, a SID, ...). Surrounding whitespace is trimmed; it must not be empty. |
| `identity_type` | `"posix_uid"` \| `"sid"` \| `"nfsv4_principal"` \| `"s3_canonical"` | yes | Kind of identifier: `posix_uid`, `sid`, `nfsv4_principal` or `s3_canonical`. |
| `source_id` | `string` (`uuid`) | yes | Source the identity is bound on. |

<a id="schema-fsbindingout"></a>
### FsBindingOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` (`date-time`) | yes | When the binding was created. |
| `fs_person_id` | `string` (`uuid`) | yes | Identity (person) the binding belongs to. |
| `groups` | array of `string` | yes | Identifiers of the groups the principal belongs to. |
| `groups_resolved_at` | `string` (`date-time`) \| `null` | yes | When the groups were last resolved automatically; null if never. |
| `groups_source` | `"manual"` \| `"auto"` \| `"claim"` \| `"ldap"` \| `"smb"` \| `"name"` | yes | Where the groups came from: `manual` (entered by hand), `auto` (resolved from the backend), or the OIDC login provenance `claim`, `ldap`, `smb`, `name`. |
| `id` | `string` (`uuid`) | yes | Binding id. |
| `identifier` | `string` | yes | The identifier (a uid, a SID, ...). |
| `identity_type` | `"posix_uid"` \| `"sid"` \| `"nfsv4_principal"` \| `"s3_canonical"` | yes | Kind of identifier: `posix_uid`, `sid`, `nfsv4_principal` or `s3_canonical`. |
| `source_id` | `string` (`uuid`) | yes | Source the binding applies to. |

<a id="schema-fsbindingpatch"></a>
### FsBindingPatch

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `groups` | array of `string` \| `null` | no | New group identifiers, replacing the current ones. |
| `groups_source` | `"manual"` \| `"auto"` \| `"claim"` \| `"ldap"` \| `"smb"` \| `"name"` \| `null` | no | Where the groups came from: `manual`, `auto`, `claim`, `ldap`, `smb` or `name`. Only callers holding `users:write` may set it. |
| `identifier` | `string` \| `null` | no | New identifier; must not be blank. |
| `identity_type` | `"posix_uid"` \| `"sid"` \| `"nfsv4_principal"` \| `"s3_canonical"` \| `null` | no | New identifier kind. |

<a id="schema-fspersonin"></a>
### FsPersonIn

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `is_primary` | `boolean` | no | Whether this is the user's primary identity. |
| `label` | `string` | yes | Display label for the identity. |

<a id="schema-fspersonout"></a>
### FsPersonOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `bindings` | array of [`FsBindingOut`](#schema-fsbindingout) | yes | The identity's per-source bindings. |
| `created_at` | `string` (`date-time`) | yes | When the identity was created. |
| `id` | `string` (`uuid`) | yes | Identity id. |
| `is_primary` | `boolean` | yes | Whether this is the user's primary identity. |
| `label` | `string` | yes | Display label. |
| `user_id` | `string` (`uuid`) | yes | User who owns the identity. |

<a id="schema-fspersonpatch"></a>
### FsPersonPatch

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `is_primary` | `boolean` \| `null` | no | New primary flag. |
| `label` | `string` \| `null` | no | New display label. |

<a id="schema-groupref-input"></a>
### GroupRef-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `identifier` | `string` | yes | Identifier of that type, e.g. a gid or a group SID. |
| `name` | `string` | no | Display name; informational only. |
| `type` | `"posix_uid"` \| `"sid"` \| `"nfsv4_principal"` \| `"s3_canonical"` \| `"cloud_drive_user"` | yes | Identity type: `posix_uid`, `sid`, `nfsv4_principal`, `s3_canonical` or `cloud_drive_user`. |

<a id="schema-groupref-output"></a>
### GroupRef-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `identifier` | `string` | yes | Identifier of that type, e.g. a gid or a group SID. |
| `name` | `string` | yes | Display name; informational only. |
| `type` | `"posix_uid"` \| `"sid"` \| `"nfsv4_principal"` \| `"s3_canonical"` \| `"cloud_drive_user"` | yes | Identity type: `posix_uid`, `sid`, `nfsv4_principal`, `s3_canonical` or `cloud_drive_user`. |

<a id="schema-handshakerequest"></a>
### HandshakeRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `hostname` | `string` \| `null` | no | Host the agent runs on. |
| `max_concurrent_units` | `integer` \| `null` | no | Work units the agent can walk at once; informational. |
| `protocol_version` | `integer` | yes | Scanner protocol version the agent speaks. This server accepts version 2 only. |
| `version` | `string` \| `null` | no | Agent build version, e.g. `0.48.0`. |

<a id="schema-handshakeresponse"></a>
### HandshakeResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `accepted` | `boolean` | yes | Whether the agent's protocol version is supported. When false the response status is 426. |
| `accepted_max` | `integer` | yes | Highest agent protocol version accepted. |
| `accepted_min` | `integer` | yes | Lowest agent protocol version accepted. |
| `config` | [`ScannerEffectiveConfig`](#schema-scannereffectiveconfig) \| `null` | yes | Effective scanner configuration; present only when accepted. |
| `reason` | `string` \| `null` | yes | Why the handshake was rejected. |
| `server_protocol_version` | `integer` | yes | Protocol version this server speaks. |

<a id="schema-heartbeatin"></a>
### HeartbeatIn

Periodic heartbeat from a running scanner. ~1 s cadence.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `bytes_scanned` | `integer` | no | Bytes read so far. |
| `current_batch_size` | `integer` \| `null` | no | Entries per ingest batch the scanner currently uses (adaptive). |
| `current_path` | `string` \| `null` | no | Path currently being walked. |
| `dirs_queued` | `integer` | no | Directories queued to walk. |
| `dirs_walked` | `integer` | no | Directories walked so far. |
| `files_scanned` | `integer` | no | Files scanned so far. |
| `files_skipped` | `integer` | no | Files skipped so far. |
| `phase` | `"prewalk"` \| `"walk"` \| `"finalize"` \| `null` | no | `prewalk`, `walk` or `finalize`. |
| `total_estimated` | `integer` \| `null` | no | Estimated total files, if known. |

<a id="schema-hostallowedscannersrequest"></a>
### HostAllowedScannersRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `scanner_ids` | array of `string` (`uuid`) | yes | Scanners that should be allowed on every source of the host; all others are removed. May be empty. |

<a id="schema-hostallowedscannersresponse"></a>
### HostAllowedScannersResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `scanners_updated` | `integer` | yes | Number of scanners whose allow-list actually changed. |
| `sources_touched` | `integer` | yes | Number of sources attached to the host. |

<a id="schema-hostcreate"></a>
### HostCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `connection_config` | `object` | yes | Type-specific connection settings shared by the attached sources, including any inline credentials. Secrets are masked in responses. Free-form: keys depend on the source or host type. |
| `credential_profile_id` | `string` (`uuid`) \| `null` | no | Credential profile to take credentials from; its type must match the host type. |
| `max_parallel_scanners` | `integer` \| `null` | no | Most distinct scanners that may work on one scan at once (1 to 16), inherited by attached sources that leave theirs unset. Null uses the built-in default. |
| `name` | `string` | yes | Unique host name. |
| `scan_chunk_size` | `integer` \| `null` | no | Entries per scan work unit (100 to 1,000,000), inherited by attached sources that leave theirs unset. Null uses the built-in default (2000). |
| `type` | `string` | yes | Host type: `smb`, `nfs` or `s3`. Local storage has no host. |

<a id="schema-hostresponse"></a>
### HostResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `connection_config` | `object` | yes | Connection settings with secrets masked as `***`. Free-form: keys depend on the source or host type. |
| `created_at` | `string` (`date-time`) | yes | When the host was created. |
| `credential_profile_id` | `string` (`uuid`) \| `null` | yes | Credential profile in use; null when none. |
| `id` | `string` (`uuid`) | yes | Host id. |
| `max_parallel_scanners` | `integer` \| `null` | yes | Scanner cap for the host's sources; null means the built-in default. |
| `name` | `string` | yes | Host name. |
| `scan_chunk_size` | `integer` \| `null` | yes | Work-unit size for the host's sources; null means the built-in default. |
| `source_count` | `integer` | yes | Number of sources attached to the host. |
| `type` | `string` | yes | Host type: `smb`, `nfs` or `s3`. |
| `updated_at` | `string` (`date-time`) | yes | When the host was last changed. |

<a id="schema-hostscannersummaryrow"></a>
### HostScannerSummaryRow

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `currently_allowed_count` | `integer` | yes | Number of the host's sources the scanner is allowed to claim. |
| `name` | `string` | yes | Scanner name. |
| `not_yet_probed_count` | `integer` | yes | Number of the host's sources this scanner has not been probed against. |
| `online` | `boolean` | yes | Whether the scanner has been seen recently (within about two minutes). |
| `pool` | `string` \| `null` | yes | Scanner pool; null when unassigned. |
| `reaches_count` | `integer` | yes | Number of the host's sources whose latest probe from this scanner succeeded. |
| `scanner_id` | `string` (`uuid`) | yes | Scanner id. |
| `total_sources` | `integer` | yes | Number of sources attached to the host. |
| `unreachable_count` | `integer` | yes | Number of the host's sources whose latest probe from this scanner failed. |

<a id="schema-hostupdate"></a>
### HostUpdate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `connection_config` | `object` \| `null` | no | Connection settings to change. A masked value (`***`) keeps the stored secret. Free-form: keys depend on the source or host type. |
| `credential_profile_id` | `string` (`uuid`) \| `null` | no | Credential profile to use; null detaches the current one. |
| `max_parallel_scanners` | `integer` \| `null` | no | New cap (1 to 16); null clears it so the built-in default applies. |
| `name` | `string` \| `null` | no | New unique host name. |
| `scan_chunk_size` | `integer` \| `null` | no | New work-unit size (100 to 1,000,000); null clears it so the built-in default applies. |

<a id="schema-identityhealth"></a>
### IdentityHealth

Identity-mapping health counters.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `unbound_count` | `integer` | yes | Filesystem identities not bound to a user. |
| `unresolved_sid_count` | `integer` | yes | SIDs whose name is unresolved (admin only). |

<a id="schema-jobout"></a>
### JobOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `error` | `string` \| `null` | yes | Error message when the job failed. |
| `finished_at` | `string` (`date-time`) \| `null` | yes | When the job finished; null while running. |
| `id` | `string` (`uuid`) | yes | Job id. |
| `kind` | `string` | yes | Job kind: `reindex_search`, `backfill_subtree_sizes`, `backfill_viewable` or `warm_groups`. |
| `params` | `object` | yes | Parameters the job was started with. Free-form: depends on the job kind. |
| `result` | `object` \| `null` | yes | Result when finished, e.g. `{"rows_affected": 1200}`. Free-form: depends on the job kind. |
| `started_at` | `string` (`date-time`) | yes | When the job started. |
| `status` | `string` | yes | `running`, `succeeded` or `failed`. |

<a id="schema-ldaploginrequest"></a>
### LDAPLoginRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `password` | `string` | yes | LDAP password. |
| `username` | `string` | yes | LDAP username. |

<a id="schema-largestfile"></a>
### LargestFile

One of the largest files the caller can see.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `filename` | `string` | yes | Base name of the file. |
| `id` | `string` (`uuid`) | yes | Entry id. |
| `mime_type` | `string` \| `null` | yes | Detected MIME type, if known. |
| `path` | `string` | yes | Full path within the source. |
| `size_bytes` | `integer` \| `null` | yes | File size in bytes. |
| `source_id` | `string` (`uuid`) | yes | Owning source id. |

<a id="schema-leaserequest"></a>
### LeaseRequest

Optional lease-poll body. v0.30.2 — the agent reports its
running build version so `scanners.version` reflects the live
binary even after an in-place image upgrade (the claim-time
version otherwise goes stale). Older agents post an empty object;
the field stays None and nothing changes.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `agent_version` | `string` \| `null` | no | Running agent build version; updates the version shown for the scanner. |

<a id="schema-leasedscan"></a>
### LeasedScan

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_jwt` | `string` \| `null` | yes | Short-lived ingest token for the agent; null if none could be minted. |
| `scan_id` | `string` (`uuid`) | yes | Leased scan id. |
| `scan_type` | `string` | yes | `full` or `incremental`. |
| `source` | [`LeasedSource`](#schema-leasedsource) | yes | Source to scan. |

<a id="schema-leasedsource"></a>
### LeasedSource

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `connection_config` | `object` | yes | Merged host and source connection config, secrets included. Free-form: keys depend on the source or host type. |
| `exclude_patterns` | array of `string` \| `null` | yes | Glob patterns to skip while scanning. |
| `id` | `string` (`uuid`) | yes | Source id. |
| `max_parallel_scanners` | `integer` | yes | Resolved cap on cooperating scanners for this scan. |
| `scan_chunk_size` | `integer` | yes | Resolved work-unit entry budget. |
| `type` | `string` | yes | Connector type. |

<a id="schema-linkedentryref"></a>
### LinkedEntryRef

The app-side entry a backing file is linked to.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` (`uuid`) | yes | Linked entry id. |
| `link_role` | `string` | yes | Role of this backing file relative to the linked entry. |
| `name` | `string` | yes | Base name of the linked entry. |
| `path` | `string` | yes | Full path of the linked entry within its source. |
| `source_id` | `string` (`uuid`) | yes | Source of the linked entry. |
| `source_name` | `string` | yes | Name of that source. |

<a id="schema-listsharesresponse"></a>
### ListSharesResponse

POST /api/v1/hosts/{id}/list-shares result.

`shares` is the enumerated list (possibly empty) on success. On
failure `step`/`error` carry the same step:reason classification
used by the source-tester (`connect`, `auth`, `mount`, `list`,
`config`); `shares` is empty in that case.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `already_added` | array of `string` | yes | The entries of `shares` that already have a source attached to this host (compared by share key, not source name). |
| `error` | `string` \| `null` | yes | Failure message; null on success. |
| `shares` | array of `string` | yes | Names of the shares, exports or buckets found; empty on failure. |
| `step` | `string` \| `null` | yes | Step that failed (`connect`, `auth`, `mount`, `list` or `config`); null on success. |

<a id="schema-logbatchin"></a>
### LogBatchIn

Up to 200 lines per request — the scanner debounces 10 lines or 500 ms.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `lines` | array of [`LogLineIn`](#schema-loglinein) | yes | Up to 200 lines. |

<a id="schema-logentryout"></a>
### LogEntryOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` (`uuid`) | yes | Log line id. |
| `level` | `string` | yes | `info`, `warn`, `error` or `stderr`. |
| `message` | `string` | yes | Log message. |
| `scanner_id` | `string` (`uuid`) \| `null` | yes | Scanner that produced the line. |
| `scanner_name` | `string` \| `null` | yes | Name of that scanner when the line was written. |
| `ts` | `string` (`date-time`) | yes | When the line was produced. |

<a id="schema-loglinein"></a>
### LogLineIn

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `level` | `"info"` \| `"warn"` \| `"error"` | yes | `info`, `warn` or `error`. |
| `message` | `string` | yes | Log message, at most 8192 characters. |
| `ts` | `string` (`date-time`) | yes | When the line was produced. |

<a id="schema-logoutresponse"></a>
### LogoutResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ok` | `boolean` | yes | Always true; logout is idempotent. |

<a id="schema-meresponse"></a>
### MeResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `auth_provider` | `string` | yes | Where the account authenticates: `local`, `oidc` or `ldap`. |
| `created_at` | `string` (`date-time`) | yes | When the account was created. |
| `email` | `string` \| `null` | yes | Contact address, if set. |
| `id` | `string` (`uuid`) | yes | User id. |
| `last_login_at` | `string` (`date-time`) \| `null` | yes | Last successful login, if any. |
| `role` | `"viewer"` \| `"admin"` | yes | Role bundle: `admin` or `viewer`. |
| `role_pinned` | `boolean` | yes | True when an admin set the role by hand, so IdP group mapping leaves it alone. |
| `scopes` | array of `string` | yes | Effective scopes of the calling credential, sorted. |
| `username` | `string` | yes | Login name. |

<a id="schema-meiliactivity"></a>
### MeiliActivity

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `documents_in_index` | `integer` \| `null` | yes | Documents in the file index; null if unreadable. |
| `error` | `string` \| `null` | yes | Why Meilisearch could not be read. |
| `last_task_at` | `string` \| `null` | yes | Time the latest index task finished or was enqueued, as reported by Meilisearch. |
| `last_task_status` | `string` \| `null` | yes | Status of the latest index task, e.g. `succeeded`. |
| `ok` | `boolean` | yes | False when Meilisearch could not be reached. |
| `pending_tasks` | `integer` \| `null` | yes | Enqueued or processing index tasks; null if unreadable. |

<a id="schema-nfsv4ace-input"></a>
### NfsV4ACE-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ace_type` | `"allow"` \| `"deny"` \| `"audit"` \| `"alarm"` | yes | `allow`, `deny`, `audit` or `alarm`. |
| `flags` | array of `string` | no | Flags on the entry, e.g. `identifier_group` for a group principal. |
| `mask` | array of `string` | no | Permission names granted or denied, e.g. `READ_DATA`, `WRITE_DATA`. |
| `principal` | `string` | yes | Principal, e.g. `alice@example.com`, `GROUP:staff@example.com` or `EVERYONE@`. |

<a id="schema-nfsv4ace-output"></a>
### NfsV4ACE-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ace_type` | `"allow"` \| `"deny"` \| `"audit"` \| `"alarm"` | yes | `allow`, `deny`, `audit` or `alarm`. |
| `flags` | array of `string` | yes | Flags on the entry, e.g. `identifier_group` for a group principal. |
| `mask` | array of `string` | yes | Permission names granted or denied, e.g. `READ_DATA`, `WRITE_DATA`. |
| `principal` | `string` | yes | Principal, e.g. `alice@example.com`, `GROUP:staff@example.com` or `EVERYONE@`. |

<a id="schema-nfsv4acl-input"></a>
### NfsV4ACL-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `entries` | array of [`NfsV4ACE-Input`](#schema-nfsv4ace-input) | yes | ACL entries in evaluation order. |
| `type` | `"nfsv4"` | yes | Discriminator, always `nfsv4`. |

<a id="schema-nfsv4acl-output"></a>
### NfsV4ACL-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `entries` | array of [`NfsV4ACE-Output`](#schema-nfsv4ace-output) | yes | ACL entries in evaluation order. |
| `type` | `"nfsv4"` | yes | Discriminator, always `nfsv4`. |

<a id="schema-ntace-input"></a>
### NtACE-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ace_type` | `"allow"` \| `"deny"` \| `"audit"` | yes | `allow`, `deny` or `audit`. |
| `flags` | array of `string` | no | Inheritance and audit flags on the entry, as reported by the scanner. |
| `mask` | array of `string` | no | Access right names granted or denied, e.g. `READ_DATA`. |
| `name` | `string` | no | Resolved account name, when known; empty otherwise. |
| `sid` | `string` | yes | Security identifier the entry applies to. |

<a id="schema-ntace-output"></a>
### NtACE-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ace_type` | `"allow"` \| `"deny"` \| `"audit"` | yes | `allow`, `deny` or `audit`. |
| `flags` | array of `string` | yes | Inheritance and audit flags on the entry, as reported by the scanner. |
| `mask` | array of `string` | yes | Access right names granted or denied, e.g. `READ_DATA`. |
| `name` | `string` | yes | Resolved account name, when known; empty otherwise. |
| `sid` | `string` | yes | Security identifier the entry applies to. |

<a id="schema-ntacl-input"></a>
### NtACL-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `control` | array of `string` | no | Security descriptor control flags, e.g. `owner_defaulted`. |
| `entries` | array of [`NtACE-Input`](#schema-ntace-input) | yes | ACL entries in evaluation order. |
| `group` | [`NtPrincipal-Input`](#schema-ntprincipal-input) \| `null` | no | Primary group of the object; null when not reported. |
| `owner` | [`NtPrincipal-Input`](#schema-ntprincipal-input) \| `null` | no | Owner of the object; null when not reported. |
| `type` | `"nt"` | yes | Discriminator, always `nt`. |

<a id="schema-ntacl-output"></a>
### NtACL-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `control` | array of `string` | yes | Security descriptor control flags, e.g. `owner_defaulted`. |
| `entries` | array of [`NtACE-Output`](#schema-ntace-output) | yes | ACL entries in evaluation order. |
| `group` | [`NtPrincipal-Output`](#schema-ntprincipal-output) \| `null` | yes | Primary group of the object; null when not reported. |
| `owner` | [`NtPrincipal-Output`](#schema-ntprincipal-output) \| `null` | yes | Owner of the object; null when not reported. |
| `type` | `"nt"` | yes | Discriminator, always `nt`. |

<a id="schema-ntprincipal-input"></a>
### NtPrincipal-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | no | Resolved account name, when known; empty otherwise. |
| `sid` | `string` | yes | Security identifier, e.g. `S-1-5-21-...`. |

<a id="schema-ntprincipal-output"></a>
### NtPrincipal-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes | Resolved account name, when known; empty otherwise. |
| `sid` | `string` | yes | Security identifier, e.g. `S-1-5-21-...`. |

<a id="schema-oauthaccesstokenrequest"></a>
### OAuthAccessTokenRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `source_id` | `string` (`uuid`) | yes | Leased source to mint a token for. |

<a id="schema-oauthaccesstokenresponse"></a>
### OAuthAccessTokenResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `access_token` | `string` | yes | Fresh provider access token. |
| `expires_at` | `string` (`date-time`) \| `null` | yes | When the access token expires, if the provider says. |

<a id="schema-oauthappconfigsummary"></a>
### OAuthAppConfigSummary

The client_secret is never returned. ``has_secret`` lets the UI
show "configured" without exposing the value or the encrypted blob.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `client_id` | `string` | yes | Configured client id; empty when not configured. |
| `configured_at` | `string` (`date-time`) | yes | When first configured; the epoch when not configured. |
| `has_secret` | `boolean` | yes | Whether a client secret is stored. |
| `provider` | `"google"` \| `"microsoft"` \| `"dropbox"` | yes | Provider key: `google`, `microsoft` or `dropbox`. |
| `redirect_uri` | `string` | yes | Configured redirect URI; empty when not configured. |
| `updated_at` | `string` (`date-time`) | yes | When last changed; the epoch when not configured. |

<a id="schema-oauthappconfigupsert"></a>
### OAuthAppConfigUpsert

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `client_id` | `string` | yes | OAuth client id from the provider's console. |
| `client_secret` | `string` \| `null` | no | OAuth client secret. Required when creating; omit or leave empty on update to keep the stored secret. Write-only. |
| `redirect_uri` | `string` | yes | Redirect URI registered with the provider; must point at `GET /api/v1/oauth/callback` of this server. |

<a id="schema-oauthcredentialsummary"></a>
### OAuthCredentialSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `access_token_expires_at` | `string` (`date-time`) \| `null` | yes | When the cached access token expires. |
| `account_email` | `string` \| `null` | yes | Account the credential belongs to. |
| `account_label` | `string` \| `null` | yes | Account display name. |
| `created_at` | `string` (`date-time`) | yes | When the credential was stored. |
| `id` | `string` (`uuid`) | yes | Credential id. |
| `provider` | `"google"` \| `"microsoft"` \| `"dropbox"` | yes | Provider key. |
| `scope` | `string` \| `null` | yes | Scopes granted by the provider. |
| `source_id` | `string` (`uuid`) \| `null` | yes | Source the credential is attached to; null if unattached. |
| `source_name` | `string` \| `null` | yes | Name of the attached source. |
| `updated_at` | `string` (`date-time`) | yes | When it was last refreshed or changed. |

<a id="schema-oauthrefreshresponse"></a>
### OAuthRefreshResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `access_token` | `string` | yes | Fresh provider access token. |
| `expires_at` | `string` (`date-time`) \| `null` | yes | When the access token expires. |

<a id="schema-oauthstartrequest"></a>
### OAuthStartRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `mode` | `string` | no | `associate` stores the credential; `test` verifies the account and discards it. |
| `provider` | `string` | yes | Provider key from `GET /oauth/providers`. |
| `source_id` | `string` (`uuid`) \| `null` | no | Source to attach the credential to; omit for an unattached credential. |

<a id="schema-oauthstartresponse"></a>
### OAuthStartResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `authorization_url` | `string` | yes | URL to open in a browser window to grant access. |
| `state` | `string` | yes | The signed state value embedded in the URL. |

<a id="schema-onlinecheckresponse"></a>
### OnlineCheckResponse

Result of POST /api/v1/hosts/{id}/online-check.

Pure TCP probe from the API process — opens a socket to the
server's port (SMB→445, NFS→2049) or HEADs the S3 endpoint. No
credentials, no share listing. Answers "is the server up on the
network from where the API sits?" — fast triage when reachability
fails (so the user can tell DNS/firewall apart from credentials).

Reachability proper is what the scanners report — see
`POST /api/v1/sources/{id}/test-scanners`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `checked_at` | `string` (`date-time`) | yes | When the probe ran. |
| `result` | [`TestResult`](#schema-testresult) | yes | Outcome of the network probe. |

<a id="schema-orphanmatchcount"></a>
### OrphanMatchCount

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `count` | `integer` | yes | Orphaned entries whose path matches an entry of the source. |

<a id="schema-overviewout"></a>
### OverviewOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `entries_total` | `integer` | yes | Indexed entries (files and directories) across all sources. |
| `log_retention_days` | `integer` | yes | Days a finished scan's logs are kept. |
| `meili_documents` | `integer` \| `null` | yes | Documents in the search index; null if the search service could not be reached. |
| `scan_log_purgeable` | `integer` | yes | Log rows of scans that ended more than the retention period ago; the hourly sweep deletes these. |
| `scan_log_rows` | `integer` | yes | Scan log rows stored. |
| `scanners_online` | `integer` | yes | Scanners seen within the last 90 seconds. |
| `scanners_total` | `integer` | yes | Number of registered scanners. |
| `scans_active` | `integer` | yes | Scans that are `pending` or `running`. |
| `scans_by_status` | map of `integer` | yes | Number of scans in each status (`pending`, `running`, `completed`, `failed`, `cancelled`). |
| `sources_total` | `integer` | yes | Number of sources. |

<a id="schema-ownerdistribution"></a>
### OwnerDistribution

Owners of the latest snapshot, largest first.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `owners` | array of [`OwnerShare`](#schema-ownershare) | yes | Owners ordered by bytes descending. |
| `taken_at` | `string` \| `null` | yes | Snapshot time, ISO 8601; null when the source has no snapshot. |

<a id="schema-ownershare"></a>
### OwnerShare

One owner's totals in the latest snapshot.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `bytes` | `integer` | yes | Total bytes owned. |
| `n` | `integer` | yes | Number of files owned. |
| `owner` | `string` | yes | Owner name (or SID/uid token when unresolved). |

<a id="schema-page-auditeventout-"></a>
### Page_AuditEventOut_

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `items` | array of [`AuditEventOut`](#schema-auditeventout) | yes | The page of results. |
| `limit` | `integer` | yes | Maximum number of items requested for this page. |
| `next_cursor` | `string` \| `null` | yes | Opaque cursor for the next page; null when this is the last page. |
| `total` | `integer` \| `null` | yes | Total matching items, when the endpoint computes it (see the endpoint description); otherwise null. |

<a id="schema-page-duplicatefile-"></a>
### Page_DuplicateFile_

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `items` | array of [`DuplicateFile`](#schema-duplicatefile) | yes | The page of results. |
| `limit` | `integer` | yes | Maximum number of items requested for this page. |
| `next_cursor` | `string` \| `null` | yes | Opaque cursor for the next page; null when this is the last page. |
| `total` | `integer` \| `null` | yes | Total matching items, when the endpoint computes it (see the endpoint description); otherwise null. |

<a id="schema-page-duplicategroup-"></a>
### Page_DuplicateGroup_

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `items` | array of [`DuplicateGroup`](#schema-duplicategroup) | yes | The page of results. |
| `limit` | `integer` | yes | Maximum number of items requested for this page. |
| `next_cursor` | `string` \| `null` | yes | Opaque cursor for the next page; null when this is the last page. |
| `total` | `integer` \| `null` | yes | Total matching items, when the endpoint computes it (see the endpoint description); otherwise null. |

<a id="schema-page-entryresponse-"></a>
### Page_EntryResponse_

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `items` | array of [`EntryResponse`](#schema-entryresponse) | yes | The page of results. |
| `limit` | `integer` | yes | Maximum number of items requested for this page. |
| `next_cursor` | `string` \| `null` | yes | Opaque cursor for the next page; null when this is the last page. |
| `total` | `integer` \| `null` | yes | Total matching items, when the endpoint computes it (see the endpoint description); otherwise null. |

<a id="schema-page-logentryout-"></a>
### Page_LogEntryOut_

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `items` | array of [`LogEntryOut`](#schema-logentryout) | yes | The page of results. |
| `limit` | `integer` | yes | Maximum number of items requested for this page. |
| `next_cursor` | `string` \| `null` | yes | Opaque cursor for the next page; null when this is the last page. |
| `total` | `integer` \| `null` | yes | Total matching items, when the endpoint computes it (see the endpoint description); otherwise null. |

<a id="schema-page-scanresponse-"></a>
### Page_ScanResponse_

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `items` | array of [`ScanResponse`](#schema-scanresponse) | yes | The page of results. |
| `limit` | `integer` | yes | Maximum number of items requested for this page. |
| `next_cursor` | `string` \| `null` | yes | Opaque cursor for the next page; null when this is the last page. |
| `total` | `integer` \| `null` | yes | Total matching items, when the endpoint computes it (see the endpoint description); otherwise null. |

<a id="schema-perscannerhistory"></a>
### PerScannerHistory

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `outcomes` | array of [`ReachabilityOutcome`](#schema-reachabilityoutcome) | yes | Recent outcomes, newest first. |
| `scanner_id` | `string` (`uuid`) \| `null` | yes | Scanner id; null for results not tied to a scanner. |
| `scanner_name` | `string` \| `null` | yes | Scanner name. |

<a id="schema-posixace-input"></a>
### PosixACE-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `perms` | `string` | yes | Three characters from `rwx-`, e.g. `r-x`. |
| `qualifier` | `string` | no | Uid or gid the entry applies to; empty for the owner, owning group, mask and other. |
| `tag` | `string` | yes | POSIX ACL tag, e.g. `user_obj`, `user`, `group_obj`, `group`, `mask` or `other`. |

<a id="schema-posixace-output"></a>
### PosixACE-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `perms` | `string` | yes | Three characters from `rwx-`, e.g. `r-x`. |
| `qualifier` | `string` | yes | Uid or gid the entry applies to; empty for the owner, owning group, mask and other. |
| `tag` | `string` | yes | POSIX ACL tag, e.g. `user_obj`, `user`, `group_obj`, `group`, `mask` or `other`. |

<a id="schema-posixacl-input"></a>
### PosixACL-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `default_entries` | array of [`PosixACE-Input`](#schema-posixace-input) \| `null` | no | Default ACL entries inherited by new children of a directory; null when none. |
| `entries` | array of [`PosixACE-Input`](#schema-posixace-input) | yes | Access ACL entries. |
| `type` | `"posix"` | yes | Discriminator, always `posix`. |

<a id="schema-posixacl-output"></a>
### PosixACL-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `default_entries` | array of [`PosixACE-Output`](#schema-posixace-output) \| `null` | yes | Default ACL entries inherited by new children of a directory; null when none. |
| `entries` | array of [`PosixACE-Output`](#schema-posixace-output) | yes | Access ACL entries. |
| `type` | `"posix"` | yes | Discriminator, always `posix`. |

<a id="schema-previewresponse"></a>
### PreviewResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `binary` | `boolean` | yes | True when the file looks binary (a NUL byte in its first 4 KiB), in which case no text is returned. |
| `byte_size_total` | `integer` | yes | Size of the whole file in bytes. |
| `encoding` | `string` \| `null` | yes | Text encoding used, `utf-8` or `latin-1`; null for binary files. |
| `text` | `string` \| `null` | yes | Decoded text of the first 64 KiB; null for binary files. |
| `truncated` | `boolean` | yes | True when the file is larger than the preview. |

<a id="schema-principalaccessresponse"></a>
### PrincipalAccessResponse

Principal-to-files lookup (`?principal=`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `by_source` | array of [`AccessSourceRollup`](#schema-accesssourcerollup) | yes | Per-source rollup, largest first. |
| `limit` | `integer` | yes | Page size requested. |
| `next_cursor` | `string` \| `null` | yes | Opaque cursor for the next page of `sample`; null at the end. |
| `principal` | [`AccessPrincipal`](#schema-accessprincipal) | yes | The looked-up principal. |
| `right` | `string` | yes | `read`, `write` or `delete`. |
| `sample` | array of [`AccessSampleHit`](#schema-accesssamplehit) | yes | One page of reachable files. |
| `summary` | [`AccessSummary`](#schema-accesssummary) | yes | Totals. |

<a id="schema-principalref-input"></a>
### PrincipalRef-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `identifier` | `string` | yes | Identifier of that type, e.g. a uid, a SID or an NFSv4 principal. |
| `name` | `string` | no | Display name; informational only. |
| `type` | `"posix_uid"` \| `"sid"` \| `"nfsv4_principal"` \| `"s3_canonical"` \| `"cloud_drive_user"` | yes | Identity type: `posix_uid`, `sid`, `nfsv4_principal`, `s3_canonical` or `cloud_drive_user`. |

<a id="schema-principalref-output"></a>
### PrincipalRef-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `identifier` | `string` | yes | Identifier of that type, e.g. a uid, a SID or an NFSv4 principal. |
| `name` | `string` | yes | Display name; informational only. |
| `type` | `"posix_uid"` \| `"sid"` \| `"nfsv4_principal"` \| `"s3_canonical"` \| `"cloud_drive_user"` | yes | Identity type: `posix_uid`, `sid`, `nfsv4_principal`, `s3_canonical` or `cloud_drive_user`. |

<a id="schema-probereport"></a>
### ProbeReport

Result body for POST /probes/{request_id}/report.

`source_id` is a copy of the value the API sent in the originating
ProbeRequest. The agent forwards it back so the API doesn't need
request → source state in memory between long-poll delivery and
the report POST (the API process that delivered the request may not
even be the same one receiving the report under horizontal scale).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `error` | `string` \| `null` | no | Error message when it failed. |
| `ok` | `boolean` | yes | Whether the source was reachable. |
| `source_id` | `string` (`uuid`) | yes | Source that was probed. |
| `step` | `string` \| `null` | no | Step that failed, e.g. `connect` or `auth`. |

<a id="schema-proberequest"></a>
### ProbeRequest

One probe delivered to the agent via /probes/long-poll.

The agent dials the source with the merged host+source
connection_config (secrets included; same envelope the scan-lease
response uses) and POSTs back to /probes/{request_id}/report.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `connection_config` | `object` | yes | Merged host and source connection config, secrets included. Free-form: keys depend on the source or host type. |
| `request_id` | `string` (`uuid`) | yes | Probe request id; echoed back in the report URL. |
| `source_id` | `string` (`uuid`) | yes | Source to probe. |
| `source_type` | `string` | yes | Connector type of the source. |

<a id="schema-providersresponse"></a>
### ProvidersResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ldap` | `boolean` | yes | Whether LDAP login is enabled. |
| `local` | `boolean` | yes | Local username and password login; always available. |
| `oidc` | `boolean` | yes | Whether OIDC single sign-on is enabled. |
| `setup_required` | `boolean` | yes | True while no user exists and first-run registration is open; it never becomes true again once the first user is created. |

<a id="schema-publicaccessblock"></a>
### PublicAccessBlock

S3 bucket-level Public Access Block settings.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `block_public_acls` | `boolean` | yes | Reject new public ACLs. |
| `block_public_policy` | `boolean` | yes | Reject public bucket policies. |
| `ignore_public_acls` | `boolean` | yes | Ignore existing public ACLs. |
| `restrict_public_buckets` | `boolean` | yes | Restrict access to buckets with public policies. |

<a id="schema-purgedeletedresponse"></a>
### PurgeDeletedResponse

Outcome of purging old soft-deleted entries.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `files_removed` | `integer` | yes | Files permanently removed. |
| `threshold_days` | `integer` | yes | Age threshold that was applied, in days. |
| `total_removed` | `integer` | yes | Files plus directories permanently removed. |

<a id="schema-purgelogsout"></a>
### PurgeLogsOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `deleted` | `integer` | yes | Log rows deleted. |

<a id="schema-purgelogsrequest"></a>
### PurgeLogsRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `older_than_days` | `integer` \| `null` | no | Delete logs of finished scans that completed more than this many days ago (`0` for all finished scans). Mutually exclusive with `scan_id`. |
| `scan_id` | `string` (`uuid`) \| `null` | no | Delete all logs of this one scan, whatever its status. Mutually exclusive with `older_than_days`. |

<a id="schema-purgesourceresponse"></a>
### PurgeSourceResponse

Outcome of purging a source's entries.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `directories_removed` | `integer` | yes | Directories deleted from the index. |
| `files_removed` | `integer` | yes | Files deleted from the index. |
| `source_id` | `string` (`uuid`) | yes | Purged source id. |
| `total_removed` | `integer` | yes | Files plus directories removed. |

<a id="schema-reachabilityhistory"></a>
### ReachabilityHistory

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `per_scanner` | array of [`PerScannerHistory`](#schema-perscannerhistory) | yes | History grouped by scanner. |

<a id="schema-reachabilityoutcome"></a>
### ReachabilityOutcome

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `completed_at` | `string` (`date-time`) | yes | When the probe finished. |
| `error` | `string` \| `null` | yes | Error message, if any. |
| `ok` | `boolean` | yes | Whether the source was reachable. |
| `started_at` | `string` (`date-time`) | yes | When the probe started. |
| `step` | `string` \| `null` | yes | Step that failed, if any. |

<a id="schema-reachabilitysummary"></a>
### ReachabilitySummary

Compact source-level reachability for badges + cards.

Derived from the latest reachability_results row across all
scanners (or implicit from a successful scan). `ok=None` means
"no data yet" — no probe has run and no successful scan has
landed for this source.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `last_at` | `string` (`date-time`) \| `null` | yes | When the deciding probe or scan finished. |
| `last_error` | `string` \| `null` | yes | Probe error message, if any. |
| `last_scanner_id` | `string` (`uuid`) \| `null` | yes | Scanner that ran the latest probe. |
| `last_scanner_name` | `string` \| `null` | yes | Name of that scanner. |
| `last_step` | `string` \| `null` | yes | Probe step that failed, if any. |
| `ok` | `boolean` \| `null` | yes | True if the latest probe succeeded or a scan completed after the latest probe; false if the latest probe failed; null with no data. |

<a id="schema-reattachrequest"></a>
### ReattachRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `dry_run` | `boolean` | no | When true (default) only count what would be re-attached. |
| `strategy` | `string` | no | `path` matches path, name and kind; `path_and_hash` also requires an equal content hash. |

<a id="schema-reattachresponse"></a>
### ReattachResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ambiguous` | `integer` | yes | Orphans matching more than one entry; left alone. |
| `committed` | `boolean` | yes | True when the re-attachment was applied; false for a dry run. |
| `conflicts` | `integer` | yes | Matches that cannot be applied because of a conflict. |
| `matched` | `integer` | yes | Orphans that match exactly one entry and can be re-attached. |

<a id="schema-recentscan"></a>
### RecentScan

A recent scan row for the dashboard.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `completed_at` | `string` \| `null` | yes | Completion time, ISO 8601. |
| `files_changed` | `integer` \| `null` | yes | Files changed by the scan. |
| `files_new` | `integer` \| `null` | yes | Files added by the scan. |
| `id` | `string` (`uuid`) | yes | Scan id. |
| `scan_type` | `string` \| `null` | yes | `full` or `incremental`. |
| `source_id` | `string` (`uuid`) | yes | Source id. |
| `source_name` | `string` \| `null` | yes | Source display name. |
| `started_at` | `string` \| `null` | yes | Start time, ISO 8601. |
| `status` | `string` | yes | Scan status. |

<a id="schema-resolverequest"></a>
### ResolveRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `sids` | array of `string` | yes | SIDs to resolve, at most 256. |
| `source_id` | `string` (`uuid`) | yes | Source whose domain controller answers the lookup. |

<a id="schema-resolveresponse"></a>
### ResolveResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `resolved` | map of [`ResolvedPrincipalOut`](#schema-resolvedprincipalout) | yes | Result for each requested SID, keyed by SID. |

<a id="schema-resolvedprincipalout"></a>
### ResolvedPrincipalOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `domain` | `string` \| `null` | yes | Domain of the account, when resolved. |
| `kind` | `string` \| `null` | yes | Account kind reported by the domain (`user`, `group`, `alias`, ...), when resolved. |
| `last_attempt_at` | `string` \| `null` | yes | ISO-8601 time of the last lookup attempt; null if none was made. |
| `name` | `string` \| `null` | yes | Account name, when resolved. |
| `sid` | `string` | yes | The SID that was looked up. |
| `status` | `"resolved"` \| `"unresolved"` \| `"skipped"` \| `"error"` | yes | `resolved`, `unresolved`, `skipped` (source type has no LSARPC) or `error` (lookup failed). |

<a id="schema-rightresult"></a>
### RightResult

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `by` | array of [`ACEReference`](#schema-acereference) | yes | The ACL entries responsible for the decision. |
| `granted` | `boolean` | yes | Whether the right is granted. |

<a id="schema-s3acl-input"></a>
### S3ACL-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `grants` | array of [`S3Grant-Input`](#schema-s3grant-input) | no | Grants on the object. |
| `owner` | [`S3Owner-Input`](#schema-s3owner-input) \| `null` | no | Object owner; null when not reported. |
| `type` | `"s3"` | yes | Discriminator, always `s3`. |

<a id="schema-s3acl-output"></a>
### S3ACL-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `grants` | array of [`S3Grant-Output`](#schema-s3grant-output) | yes | Grants on the object. |
| `owner` | [`S3Owner-Output`](#schema-s3owner-output) \| `null` | yes | Object owner; null when not reported. |
| `type` | `"s3"` | yes | Discriminator, always `s3`. |

<a id="schema-s3grant-input"></a>
### S3Grant-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `grantee_id` | `string` | no | Canonical user id or group URI; empty when not applicable. |
| `grantee_name` | `string` | no | Display name or email address; empty when not reported. |
| `grantee_type` | `"canonical_user"` \| `"group"` \| `"amazon_customer_by_email"` | yes | `canonical_user`, `group` or `amazon_customer_by_email`. |
| `permission` | `"FULL_CONTROL"` \| `"READ"` \| `"WRITE"` \| `"READ_ACP"` \| `"WRITE_ACP"` | yes | `FULL_CONTROL`, `READ`, `WRITE`, `READ_ACP` or `WRITE_ACP`. |

<a id="schema-s3grant-output"></a>
### S3Grant-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `grantee_id` | `string` | yes | Canonical user id or group URI; empty when not applicable. |
| `grantee_name` | `string` | yes | Display name or email address; empty when not reported. |
| `grantee_type` | `"canonical_user"` \| `"group"` \| `"amazon_customer_by_email"` | yes | `canonical_user`, `group` or `amazon_customer_by_email`. |
| `permission` | `"FULL_CONTROL"` \| `"READ"` \| `"WRITE"` \| `"READ_ACP"` \| `"WRITE_ACP"` | yes | `FULL_CONTROL`, `READ`, `WRITE`, `READ_ACP` or `WRITE_ACP`. |

<a id="schema-s3owner-input"></a>
### S3Owner-Input

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `display_name` | `string` | no | Owner display name; empty when not reported. |
| `id` | `string` | yes | Canonical user id of the owner. |

<a id="schema-s3owner-output"></a>
### S3Owner-Output

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `display_name` | `string` | yes | Owner display name; empty when not reported. |
| `id` | `string` | yes | Canonical user id of the owner. |

<a id="schema-scanbatchin"></a>
### ScanBatchIn

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `entries` | array of [`EntryIn`](#schema-entryin) | yes | Entries observed, at most 10,000. Duplicate paths keep the last. |
| `inaccessible_dirs` | `integer` | no | Directories skipped in this unit or scan because they could not be read; accumulated on the scan. |
| `inaccessible_files` | `integer` | no | Files skipped because they could not be read; accumulated on the scan. |
| `is_final` | `boolean` | no | True on the scanner's last batch: finalizes a scan that does not use parallel work units. |
| `scan_id` | `string` (`uuid`) | yes | Leased scan these entries belong to; must belong to `source_id`. |
| `source_id` | `string` (`uuid`) | yes | Source being scanned. |
| `source_security_metadata` | [`SourceSecurityMetadata-Input`](#schema-sourcesecuritymetadata-input) \| `null` | no | Source-level security data (for example an S3 bucket policy) to store on the source. |

<a id="schema-scanbatchresponse"></a>
### ScanBatchResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `extract_candidates` | array of [`ExtractCandidate`](#schema-extractcandidate) | yes | New or changed files whose text the scanner should extract and send to `POST /ingest/content`. |
| `files_processed` | `integer` | yes | Files in the batch that were processed. |
| `scan_id` | `string` (`uuid`) | yes | Scan the batch belonged to. |

<a id="schema-scanresponse"></a>
### ScanResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `bytes_scanned_so_far` | `integer` \| `null` | yes | Bytes read so far, from the latest heartbeat. |
| `completed_at` | `string` (`date-time`) \| `null` | yes | When the scan ended; null while active. |
| `current_path` | `string` \| `null` | yes | Path being walked, from the latest heartbeat. |
| `dirs_queued` | `integer` | yes | Directories waiting to be walked. |
| `dirs_walked` | `integer` | yes | Directories walked so far. |
| `error_message` | `string` \| `null` | yes | Failure or cancellation detail. |
| `files_changed` | `integer` | yes | Files whose metadata changed. |
| `files_deleted` | `integer` | yes | Files no longer present. |
| `files_found` | `integer` | yes | Files seen so far (final once the scan ends). |
| `files_new` | `integer` | yes | Files new since the previous scan. |
| `files_skipped` | `integer` | yes | Files skipped (excluded or unreadable) so far. |
| `id` | `string` (`uuid`) | yes | Scan id. |
| `inaccessible_dirs` | `integer` | yes | Directories the scanner could not read (permission denied or vanished mid-scan). |
| `inaccessible_files` | `integer` | yes | Files the scanner could not read. |
| `last_heartbeat_at` | `string` (`date-time`) \| `null` | yes | Time of the latest heartbeat. |
| `phase` | `string` \| `null` | yes | `prewalk`, `walk` or `finalize`. |
| `previous_scan_files` | `integer` \| `null` | yes | File count of the previous scan, for progress estimates. |
| `scan_type` | `string` | yes | `incremental` or `full`. |
| `source_id` | `string` (`uuid`) | yes | Source scanned. |
| `started_at` | `string` (`date-time`) \| `null` | yes | When the scan started; null while waiting for a scanner. |
| `status` | `string` | yes | `pending`, `running`, `completed`, `failed` or `cancelled`. |
| `total_estimated` | `integer` \| `null` | yes | Estimated total files, when the scanner could pre-count. |

<a id="schema-scantriggerrequest"></a>
### ScanTriggerRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `scan_type` | `"incremental"` \| `"full"` | no | `incremental` (default) walks and compares against the previous scan; `full` re-reads everything. |
| `source_id` | `string` (`uuid`) \| `null` | no | Id of the source to scan. Give this or `source_name`. |
| `source_name` | `string` \| `null` | no | Name of the source to scan. Give this or `source_id`. |

<a id="schema-scantriggerresponse"></a>
### ScanTriggerResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created` | `boolean` | yes | False when an existing pending or running scan was returned instead of creating one. |
| `last_scan_at` | `string` \| `null` | yes | ISO-8601 time of the source's last completed scan; null if never. |
| `scan_id` | `string` (`uuid`) | yes | The queued scan, or the scan already pending or running for the source. |
| `scan_type` | `string` | yes | `incremental` or `full`. |
| `source_id` | `string` (`uuid`) | yes | Source being scanned. |
| `source_name` | `string` | yes | Source name. |

<a id="schema-scannercounts"></a>
### ScannerCounts

Lightweight count summary for the Sources page banner — avoids
re-fetching the whole scanner list per page mount.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `online` | `integer` | yes | Scanners seen within the last 90 seconds. |
| `registered` | `integer` | yes | Registered scanners. |

<a id="schema-scannercreate"></a>
### ScannerCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes | Unique scanner name. |
| `pool` | `string` | no | Pool the scanner serves; scans are only leased to scanners in their pool. |

<a id="schema-scannercreated"></a>
### ScannerCreated

Returned ONCE on /POST /api/v1/scanners. The private_key_pem field
isn't persisted on the api side — copy it now or rotate later.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` (`uuid`) | yes | Scanner id (the JWT `kid` and `sub`). |
| `key_fingerprint` | `string` | yes | Fingerprint of the public key. |
| `name` | `string` | yes | Scanner name. |
| `pool` | `string` | yes | Scanner pool. |
| `private_key_pem` | `string` | yes | Private key, shown only in this response. Install it on the scanner host. |
| `protocol_version` | `integer` | yes | Protocol version this server speaks. |
| `public_key_pem` | `string` | yes | Public key the server verifies JWTs with. |

<a id="schema-scannereffectiveconfig"></a>
### ScannerEffectiveConfig

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `extract_workers` | `integer` | yes | Text extraction workers. |
| `max_concurrent_units` | `integer` | yes | Work units walked at once. |
| `tika_url` | `string` | yes | Tika URL the agent uses; empty when extraction is off. |

<a id="schema-scannerpatch"></a>
### ScannerPatch

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `allowed_scan_types` | array of `string` \| `null` | no | Restrict the scanner to these scan types (`incremental`, `full`). Omit to leave unchanged. |
| `allowed_source_ids` | array of `string` (`uuid`) \| `null` | no | Restrict the scanner to these sources. Omit to leave unchanged. |
| `clear_allowed_scan_types` | `boolean` | no | Remove the scan type restriction. |
| `clear_allowed_source_ids` | `boolean` | no | Remove the source restriction. |
| `clear_extract_workers` | `boolean` | no | Revert to the default worker count. |
| `clear_max_concurrent_units` | `boolean` | no | Revert to the default of 1. |
| `clear_tika_url` | `boolean` | no | Revert to the server's Tika URL. |
| `enabled` | `boolean` \| `null` | no | Enable or disable; a disabled scanner cannot authenticate. |
| `extract_workers` | `integer` \| `null` | no | Concurrent text extraction workers (1-64). |
| `max_concurrent_units` | `integer` \| `null` | no | Work units walked at once (1-32). |
| `name` | `string` \| `null` | no | New unique name. |
| `pool` | `string` \| `null` | no | New pool. |
| `tika_url` | `string` \| `null` | no | Tika server for text extraction, `http://` or `https://`; an empty string disables extraction on this scanner. |

<a id="schema-scannerreachabilityrow"></a>
### ScannerReachabilityRow

One row in the source detail's eligibility checklist.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `currently_allowed` | `boolean` | yes | Whether the scanner may claim work for the source. |
| `error` | `string` \| `null` | yes | Probe error message, if any. |
| `last_probed_at` | `string` (`date-time`) \| `null` | yes | When the latest probe finished. |
| `name` | `string` | yes | Scanner name. |
| `ok` | `boolean` \| `null` | yes | Latest probe outcome; null when never probed. |
| `online` | `boolean` | yes | Whether the scanner was seen within the last 2 minutes. |
| `pool` | `string` \| `null` | yes | Scanner pool. |
| `scanner_id` | `string` (`uuid`) | yes | Scanner id. |
| `step` | `string` \| `null` | yes | Probe step that failed, if any. |

<a id="schema-scannersummary"></a>
### ScannerSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `allowed_scan_types` | array of `string` \| `null` | yes | Scan types the scanner is restricted to; null for any. |
| `allowed_source_ids` | array of `string` (`uuid`) \| `null` | yes | Sources the scanner is restricted to; null for any. |
| `effective_config` | [`ScannerEffectiveConfig`](#schema-scannereffectiveconfig) \| `null` | yes | The configuration the agent actually receives. |
| `enabled` | `boolean` | yes | Whether the scanner may authenticate. |
| `extract_workers` | `integer` \| `null` | yes | Admin override of the extraction workers; null inherits the default. |
| `hostname` | `string` \| `null` | yes | Host the agent reported at handshake. |
| `id` | `string` (`uuid`) | yes | Scanner id. |
| `key_fingerprint` | `string` | yes | Fingerprint of the registered public key. |
| `last_seen_at` | `string` (`date-time`) \| `null` | yes | Last time the scanner made any request. |
| `max_concurrent_units` | `integer` \| `null` | yes | Admin override of concurrent units; null inherits the default. |
| `name` | `string` | yes | Scanner name. |
| `online` | `boolean` | yes | Whether it was seen within the last 90 seconds. |
| `pool` | `string` | yes | Pool the scanner serves. |
| `protocol_version` | `integer` \| `null` | yes | Protocol version the agent reported. |
| `registered_at` | `string` (`date-time`) | yes | When the scanner was registered. |
| `tika_url` | `string` \| `null` | yes | Admin override of the Tika URL; null inherits the server setting. |
| `version` | `string` \| `null` | yes | Agent build version. |

<a id="schema-scopecatalog"></a>
### ScopeCatalog

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `roles` | map of array of `string` | yes | Scopes granted by each role, keyed by role name. |
| `scopes` | array of [`ScopeInfo`](#schema-scopeinfo) | yes | Every scope the API defines. |

<a id="schema-scopeinfo"></a>
### ScopeInfo

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `allowed_with_source_limit` | `boolean` | yes | Whether a token limited to sources may hold this scope. False scopes are refused (422) at creation. |
| `description` | `string` | yes | What the scope permits. |
| `name` | `string` | yes | Scope name, e.g. `files:read`. |
| `read_only` | `boolean` | yes | Whether the scope only reads (`:read` scopes, `files:content`, `credentials:list`). A read-only token preset is exactly these. |

<a id="schema-searchhit"></a>
### SearchHit

A search result from Meilisearch — uses only fields that are indexed.

`source_id` is nullable because entries can become orphaned when their
source is deleted with ``purge_entries=False`` (default): the entry row
keeps existing in postgres with ``source_id=NULL``, and the Meili doc
is updated to match. Such orphans were causing 500s in the search
handler before v0.5.10 because the schema rejected null. The api
filters them out of the response by default — see
routers/search.py — so they stay invisible until reattached, but the
schema stays lenient so a single straggler can't crash the whole
response.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `content_hash` | `string` \| `null` | yes | Content hash; null for directories and unhashed files. |
| `content_text` | `string` \| `null` | yes | Matched extracted text, when the index returns it. |
| `dup_count` | `integer` | yes | Number of other files with the same content hash within the caller's visible sources. |
| `extension` | `string` \| `null` | yes | Extension without the dot. |
| `filename` | `string` | yes | Base name. |
| `fs_modified_at` | `integer` \| `null` | yes | Modification time as Unix seconds. |
| `id` | `string` (`uuid`) | yes | Entry id. |
| `link_role` | `string` \| `null` | yes | Role of this file relative to the Immich/Paperless entry it is linked to (original, thumbnail, encoded_video, sidecar, archive or version); null when unlinked. |
| `linked_entry_id` | `string` (`uuid`) \| `null` | yes | Id of the Immich/Paperless entry this file is linked to; null when unlinked. |
| `mime_type` | `string` \| `null` | yes | Detected MIME type. |
| `path` | `string` | yes | Full path within the source. |
| `size_bytes` | `integer` \| `null` | yes | Size in bytes. |
| `source_id` | `string` (`uuid`) \| `null` | yes | Source id; null for an orphaned entry whose source was deleted. |
| `tags` | array of `string` | yes | Tags on the file. |

<a id="schema-searchpage"></a>
### SearchPage

One page of search results. `total` is the (estimated) number of matches.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `facet_distribution` | map of map of `integer` \| `null` | yes | Facet counts keyed by field (e.g. `domain_metadata.correspondent`); null on the SQL fallback path. |
| `items` | array of [`SearchHit`](#schema-searchhit) | yes | The page of results. |
| `limit` | `integer` | yes | Maximum number of items requested for this page. |
| `next_cursor` | `string` \| `null` | yes | Opaque cursor for the next page; null when this is the last page. |
| `query` | `string` | yes | The query that produced these results. |
| `total` | `integer` \| `null` | yes | Total matching items, when the endpoint computes it (see the endpoint description); otherwise null. |

<a id="schema-seriespoint"></a>
### SeriesPoint

One snapshot value in a time series.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `taken_at` | `string` | yes | Snapshot time, ISO 8601. |
| `value` | `integer` | yes | Metric value at that time (bytes or file count). |

<a id="schema-serviceprobe"></a>
### ServiceProbe

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `error` | `string` \| `null` | yes | Why the probe failed; absent when it succeeded. |
| `latency_ms` | `number` \| `null` | yes | Round-trip time in milliseconds; null when the probe failed. |
| `ok` | `boolean` | yes | Whether the service answered within 2 seconds. |

<a id="schema-servicesactivity"></a>
### ServicesActivity

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `checked_at` | `string` (`date-time`) | yes | When the data was gathered (cached for 5 seconds). |
| `meilisearch` | [`MeiliActivity`](#schema-meiliactivity) | yes | Search index activity. |
| `tika` | [`TikaActivity`](#schema-tikaactivity) | yes | Text extraction throughput. |

<a id="schema-serviceshealth"></a>
### ServicesHealth

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `checked_at` | `string` (`date-time`) | yes | When the probes ran (cached for 5 seconds). |
| `meilisearch` | [`ServiceProbe`](#schema-serviceprobe) | yes | Meilisearch probe. |
| `postgres` | [`ServiceProbe`](#schema-serviceprobe) | yes | Postgres probe. |
| `redis` | [`ServiceProbe`](#schema-serviceprobe) | yes | Redis probe. |
| `tika` | [`ServiceProbe`](#schema-serviceprobe) | yes | Tika probe. |

<a id="schema-settingdescription"></a>
### SettingDescription

A setting with its metadata and current value.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `choices` | array of `string` \| `null` | yes | Allowed values, when constrained. |
| `default` | any | yes | Default value. Free-form: the type depends on the setting. |
| `description` | `string` | yes | Human-readable explanation. |
| `group` | `string` | yes | Group key the setting belongs to. |
| `key` | `string` | yes | Setting key. |
| `restart` | `boolean` | yes | Whether changing it needs a restart. |
| `secret` | `boolean` | yes | Whether the value is a secret (returned masked). |
| `set` | `boolean` \| `null` | yes | Secrets only: whether a value is stored. |
| `type` | `"bool"` \| `"int"` \| `"str"` \| `"list[str]"` \| `"dict[str,int]"` \| `"enum"` | yes | Value type. |
| `value` | any | yes | Current value; the mask for a set secret. Free-form: the type depends on the setting. |

<a id="schema-settinggroup"></a>
### SettingGroup

A labelled group of settings.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `key` | `string` | yes | Group key. |
| `label` | `string` | yes | Group display label. |
| `settings` | array of [`SettingDescription`](#schema-settingdescription) | yes | Settings in the group. |

<a id="schema-settingpatch"></a>
### SettingPatch

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `value` | any | no | New value, coerced to the setting's type. For a secret: `null` or the mask keeps it, an empty string clears it. Free-form: the type depends on the setting. |

<a id="schema-settingvalue"></a>
### SettingValue

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `key` | `string` | yes | Setting key. |
| `set` | `boolean` \| `null` | yes | Secrets only: whether a value is stored. |
| `value` | any | yes | Current value; for a set secret, the mask. Free-form: the type depends on the setting. |

<a id="schema-settingslistresponse"></a>
### SettingsListResponse

All server settings, grouped.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `groups` | array of [`SettingGroup`](#schema-settinggroup) | yes | Setting groups in display order. |

<a id="schema-sourcecreate"></a>
### SourceCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `connection_config` | `object` | yes | Connection settings for the type (path, share, bucket, URL, ...). With a host, only share-level fields are needed. Secret-looking keys are masked on read. Free-form: keys depend on the source or host type. |
| `credential_profile_id` | `string` (`uuid`) \| `null` | no | Credential profile merged under `connection_config`; must have the same type. |
| `exclude_patterns` | array of `string` \| `null` | no | Glob patterns to skip while scanning. |
| `host_id` | `string` (`uuid`) \| `null` | no | Host that supplies hostname and credentials; must have the same type. Not allowed for hostless types such as `local`. |
| `is_removable` | `boolean` \| `null` | no | Whether the storage may be disconnected (USB or network mounts). Inferred from type and path when omitted. |
| `max_parallel_scanners` | `integer` \| `null` | no | Scanners that may work on one scan at once; null inherits from the host (default 1). |
| `name` | `string` | yes | Display name; must be unique. |
| `preferred_pool` | `string` \| `null` | no | Only scanners in this pool may scan the source; null for any pool. |
| `scan_chunk_size` | `integer` \| `null` | no | Entries per work unit; null inherits from the host (default 2000). |
| `scan_schedule` | `string` \| `null` | no | Cron expression for scheduled scans; null for manual scans only. |
| `type` | `string` | yes | Source type, e.g. `local`, `smb`, `nfs`, `s3`, `webdav`, `paperless`, `immich`, `gdrive`, `onedrive` or `dropbox`. |

<a id="schema-sourceentrycount"></a>
### SourceEntryCount

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `count` | `integer` | yes | Entries (excluding soft-deleted) in the source. |

<a id="schema-sourcelistresponse"></a>
### SourceListResponse

Lean shape for `GET /api/v1/sources`. Drops the heavy fields
(`connection_config`, `security_metadata`, `exclude_patterns`)
that the Sources list view never reads — but adds a derived
`summary` string so the per-card "user@host" / "bucket
(region)" subtitle still renders without the full config.

For typical 50-source installs this cuts the list payload
~25-30%; bigger wins on installs with rich connection_configs
(e.g. SMB/SFTP with credentials + known-host blobs).

Detail panel still uses the full `SourceResponse` via
`GET /api/v1/sources/{id}` for edit / display purposes.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` (`date-time`) | yes | When the source was created. |
| `credential_profile_id` | `string` (`uuid`) \| `null` | yes | Credential profile merged under the source's config, if any. |
| `host` | [`_HostInline`](#schema--hostinline) \| `null` | yes | Summary of that host. |
| `host_id` | `string` (`uuid`) \| `null` | yes | Host the source belongs to. |
| `id` | `string` (`uuid`) | yes | Source id. |
| `is_removable` | `boolean` | yes | Whether the storage may be disconnected. |
| `last_scan_at` | `string` (`date-time`) \| `null` | yes | When the last scan completed. |
| `max_parallel_scanners` | `integer` \| `null` | yes | Scanner cap set on the source; null inherits. |
| `name` | `string` | yes | Display name. |
| `preferred_pool` | `string` \| `null` | yes | Pool that must scan the source; null for any. |
| `scan_schedule` | `string` \| `null` | yes | Cron expression for scheduled scans; null if manual only. |
| `status` | `string` | yes | `online`, `scanning`, `failed` or `offline`. |
| `summary` | `string` | yes | Server-rendered one-line description such as `host:/export` or `bucket (region)`. |
| `type` | `string` | yes | Source type. |
| `updated_at` | `string` (`date-time`) | yes | When the source was last changed. |

<a id="schema-sourcereachabilityrow"></a>
### SourceReachabilityRow

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `currently_allowed` | `boolean` | yes | Whether the scanner may claim work for this source. |
| `error` | `string` \| `null` | yes | Probe error message, if any. |
| `host_name` | `string` \| `null` | yes | Host the source belongs to, if any. |
| `last_probed_at` | `string` (`date-time`) \| `null` | yes | When the latest probe completed. |
| `ok` | `boolean` \| `null` | yes | Latest probe outcome; null when never probed. |
| `source_id` | `string` (`uuid`) | yes | Source id. |
| `source_name` | `string` | yes | Source display name. |
| `source_type` | `string` | yes | Connector type. |
| `step` | `string` \| `null` | yes | Probe step that failed, if any. |

<a id="schema-sourceresponse"></a>
### SourceResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `connection_config` | `object` | yes | Connection settings with secret-looking values replaced by `***`. Free-form: keys depend on the source or host type. |
| `created_at` | `string` (`date-time`) | yes | When the source was created. |
| `credential_profile_id` | `string` (`uuid`) \| `null` | yes | Credential profile merged under the config. |
| `exclude_patterns` | array of `string` \| `null` | yes | Glob patterns skipped while scanning. |
| `host` | [`_HostInline`](#schema--hostinline) \| `null` | yes | Summary of that host. |
| `host_id` | `string` (`uuid`) \| `null` | yes | Host the source belongs to. |
| `id` | `string` (`uuid`) | yes | Source id. |
| `is_removable` | `boolean` | yes | Whether the storage may be disconnected. |
| `last_scan_at` | `string` (`date-time`) \| `null` | yes | When the last scan completed. |
| `max_parallel_scanners` | `integer` \| `null` | yes | Scanner cap set on the source; null inherits from the host. |
| `name` | `string` | yes | Display name. |
| `preferred_pool` | `string` \| `null` | yes | Pool that must scan the source; null for any. |
| `scan_chunk_size` | `integer` \| `null` | yes | Work unit size set on the source; null inherits from the host. |
| `scan_schedule` | `string` \| `null` | yes | Cron expression for scheduled scans; null if manual only. |
| `security_metadata` | [`SourceSecurityMetadata-Output`](#schema-sourcesecuritymetadata-output) \| `null` | yes | Source-level security data reported by the scanner (S3 sources only). |
| `status` | `string` | yes | `online`, `scanning`, `failed` or `offline`. |
| `type` | `string` | yes | Source type. |
| `updated_at` | `string` (`date-time`) | yes | When the source was last changed. |

<a id="schema-sourcesecuritymetadata-input"></a>
### SourceSecurityMetadata-Input

Source-level security posture the scanner captures at scan start (S3).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `bucket_acl` | `object` \| `null` | no | Raw S3 bucket ACL document. |
| `bucket_policy` | `object` \| `null` | no | Raw S3 bucket policy document. |
| `bucket_policy_present` | `boolean` | no | Whether the bucket has a policy. |
| `captured_at` | `string` | yes | When the scanner captured this, ISO 8601. |
| `is_public_inferred` | `boolean` | no | Whether the scanner inferred the bucket is publicly reachable. |
| `public_access_block` | [`PublicAccessBlock`](#schema-publicaccessblock) \| `null` | no | Public Access Block settings, when readable. |

<a id="schema-sourcesecuritymetadata-output"></a>
### SourceSecurityMetadata-Output

Source-level security posture the scanner captures at scan start (S3).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `bucket_acl` | `object` \| `null` | yes | Raw S3 bucket ACL document. |
| `bucket_policy` | `object` \| `null` | yes | Raw S3 bucket policy document. |
| `bucket_policy_present` | `boolean` | yes | Whether the bucket has a policy. |
| `captured_at` | `string` | yes | When the scanner captured this, ISO 8601. |
| `is_public_inferred` | `boolean` | yes | Whether the scanner inferred the bucket is publicly reachable. |
| `public_access_block` | [`PublicAccessBlock`](#schema-publicaccessblock) \| `null` | yes | Public Access Block settings, when readable. |

<a id="schema-sourceupdate"></a>
### SourceUpdate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `connection_config` | `object` \| `null` | no | Keys to merge into the stored config. A secret sent as `***` keeps its stored value. Free-form: keys depend on the source or host type. |
| `credential_profile_id` | `string` (`uuid`) \| `null` | no | Credential profile to merge under the config; same type as the source. |
| `exclude_patterns` | array of `string` \| `null` | no | New exclude patterns, replacing the list. |
| `host_id` | `string` (`uuid`) \| `null` | no | Attach to a host of the same type. Null detaches (only for hostless types). |
| `is_removable` | `boolean` \| `null` | no | New removable-storage hint. |
| `max_parallel_scanners` | `integer` \| `null` | no | New scanner cap; null inherits. |
| `name` | `string` \| `null` | no | New display name. |
| `preferred_pool` | `string` \| `null` | no | New preferred pool; null for any. |
| `scan_chunk_size` | `integer` \| `null` | no | New work unit size; null inherits. |
| `scan_schedule` | `string` \| `null` | no | New cron expression; null clears the schedule. |

<a id="schema-splitrequest"></a>
### SplitRequest

Bulk-add child units. Idempotent on (scan_id, path) — safe to
retry.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `child_paths` | array of `string` | no | Directory paths to add as pending units. |
| `parent_unit_id` | `string` (`uuid`) \| `null` | no | Unit the paths were split from; null when enqueueing the root. |

<a id="schema-splitresponse"></a>
### SplitResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created` | `integer` | yes | Units created. |
| `skipped` | `integer` | yes | Paths that already existed (idempotent retry). |

<a id="schema-startjobrequest"></a>
### StartJobRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `string` | yes | Job to run: `reindex_search`, `backfill_subtree_sizes`, `backfill_viewable` or `warm_groups`. |
| `params` | `object` | no | Optional job parameters: `batch_size` (`reindex_search`, `backfill_viewable`) and `source_id` (the three backfills and `warm_groups`). Free-form: depends on the job kind. |

<a id="schema-stderrbatchin"></a>
### StderrBatchIn

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `chunks` | array of [`StderrChunkIn`](#schema-stderrchunkin) | yes | Up to 200 chunks. |

<a id="schema-stderrchunkin"></a>
### StderrChunkIn

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `chunk` | `string` | yes | Raw stderr text, at most 4096 characters. |
| `ts` | `string` (`date-time`) | yes | When the chunk was captured. |

<a id="schema-storagebucket"></a>
### StorageBucket

Synthetic rectangle for the long tail (`other`) or filtered-out entries (`hidden`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `child_count` | `integer` | yes | Entries in the bucket. |
| `kind` | `"other"` \| `"hidden"` | yes | Bucket kind. |
| `name` | `string` | yes | `<other>` or `<hidden>`. |
| `size_bytes` | `integer` | yes | Bytes in the bucket. |

<a id="schema-storagebylinkrole"></a>
### StorageByLinkRole

Files and bytes of one source in one mirror-link bucket.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `count` | `integer` | yes | Number of non-deleted files in the bucket. |
| `link_role` | `"original"` \| `"thumbnail"` \| `"encoded_video"` \| `"sidecar"` \| `"archive"` \| `"version"` \| `"unlinked"` | yes | Mirror-link role; `unlinked` for files with no link. |
| `total_size` | `integer` | yes | Sum of `size_bytes`, in bytes. |

<a id="schema-storagebysource"></a>
### StorageBySource

Aggregate file count and bytes for one source.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `count` | `integer` | yes | Number of files in the source. |
| `logical_view` | `boolean` | yes | True when the source is the target of a mirror link; its bytes are counted on the backing source instead. |
| `source_id` | `string` (`uuid`) | yes | Source id. |
| `source_name` | `string` | yes | Source display name. |
| `total_size` | `integer` \| `null` | yes | Sum of `size_bytes`, in bytes. Always 0 for a logical-view source. |

<a id="schema-storagebytype"></a>
### StorageByType

Aggregate file count and bytes for one file extension.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `count` | `integer` | yes | Number of files with this extension. |
| `extension` | `string` \| `null` | yes | Lower-case extension without the dot; null for files with none. |
| `total_size` | `integer` \| `null` | yes | Sum of `size_bytes`, in bytes. |

<a id="schema-storagechild"></a>
### StorageChild

An immediate child of a directory.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `color_key` | `string` | yes | Bucket key for the requested `color_by` dimension. |
| `file_count` | `integer` | yes | Files contained (1 for a file). |
| `id` | `string` (`uuid`) | yes | Entry id. |
| `kind` | `string` | yes | `file` or `directory`. |
| `name` | `string` | yes | Base name. |
| `path` | `string` | yes | Full path. |
| `size_bytes` | `integer` | yes | Size (subtree size for directories). |

<a id="schema-storagechildrenresponse"></a>
### StorageChildrenResponse

Largest children of a directory plus tail buckets.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `children` | array of [`StorageChild`](#schema-storagechild) | yes | Top `limit` children by size. |
| `color_by` | `string` | yes | Colour dimension used. |
| `enforced` | `boolean` | yes | Whether permission filtering was applied. |
| `hidden` | [`StorageBucket`](#schema-storagebucket) \| `null` | yes | Aggregate hidden by permission filtering (admins only). |
| `other` | [`StorageBucket`](#schema-storagebucket) \| `null` | yes | Aggregate of children beyond `limit`. |
| `path` | `string` | yes | Normalised directory path. |
| `source_id` | `string` (`uuid`) | yes | Source id. |

<a id="schema-storagesource"></a>
### StorageSource

One source's size from its latest snapshot.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `directory_count` | `integer` | yes | Total directories. |
| `file_count` | `integer` | yes | Total files. |
| `logical_view` | `boolean` | yes | True when the source is the target of a mirror link; its bytes are counted on the backing source instead. |
| `size_bytes` | `integer` | yes | Total bytes. Always 0 for a logical-view source. |
| `source_id` | `string` (`uuid`) | yes | Source id. |
| `source_name` | `string` | yes | Source display name. |
| `source_type` | `string` | yes | Connector type. |
| `taken_at` | `string` \| `null` | yes | Snapshot time, ISO 8601; null when never scanned. |

<a id="schema-storagesourcesresponse"></a>
### StorageSourcesResponse

Cross-source treemap data.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `sources` | array of [`StorageSource`](#schema-storagesource) | yes | Sources ordered by size descending. |

<a id="schema-storagetreeresponse"></a>
### StorageTreeResponse

Recursive subtree bounded by a node budget.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `color_by` | `string` | yes | Colour dimension used. |
| `enforced` | `boolean` | yes | Whether permission filtering was applied. |
| `node_count` | `integer` | yes | Nodes returned. |
| `path` | `string` | yes | Normalised root path. |
| `root` | [`TreeNode`](#schema-treenode) \| `null` | yes | Root node; null when the path is empty or not visible. |
| `source_id` | `string` (`uuid`) | yes | Source id. |
| `truncated` | `boolean` | yes | True when the node budget was hit. |

<a id="schema-stuckscanout"></a>
### StuckScanOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `age_seconds` | `number` \| `null` | yes | Seconds since the scan started. |
| `assigned_scanner_id` | `string` (`uuid`) \| `null` | yes | Scanner holding the lease, if any. |
| `assigned_scanner_name` | `string` \| `null` | yes | Name of that scanner. |
| `last_heartbeat_at` | `string` (`date-time`) \| `null` | yes | Last progress heartbeat from the scanner. |
| `scan_id` | `string` (`uuid`) | yes | Scan id. |
| `scan_type` | `string` | yes | `full` or `incremental`. |
| `source_id` | `string` (`uuid`) \| `null` | yes | Source being scanned; null if it was deleted. |
| `source_name` | `string` \| `null` | yes | Source name. |
| `started_at` | `string` (`date-time`) \| `null` | yes | When the scan started; null while still waiting for a scanner. |
| `status` | `string` | yes | `pending` or `running`. |

<a id="schema-tagcreate"></a>
### TagCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `color` | `string` \| `null` | no | Display colour, e.g. `#2563eb`. |
| `name` | `string` | yes | Tag name; unique. |

<a id="schema-tagresponse"></a>
### TagResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `color` | `string` \| `null` | yes | Display colour, if set. |
| `id` | `string` (`uuid`) | yes | Tag id. |
| `name` | `string` | yes | Tag name. |

<a id="schema-tagusageresponse"></a>
### TagUsageResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `color` | `string` \| `null` | yes | Display colour, if set. |
| `direct_count` | `integer` | yes | Entries tagged directly. |
| `inherited_count` | `integer` | yes | Entries that inherit the tag from a tagged directory. |
| `name` | `string` | yes | Tag name. |

<a id="schema-testresult"></a>
### TestResult

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `error` | `string` \| `null` | no | Failure message; null on success. |
| `ok` | `boolean` | yes | Whether the connection succeeded. |
| `step` | `"connect"` \| `"auth"` \| `"mount"` \| `"list"` \| `"config"` \| `null` | no | Step that failed: `connect`, `auth`, `mount`, `list` or `config`; null on success. |
| `tier` | `string` \| `null` | no | NFS only: protocol path that validated the export (strong, e.g. `mount3` or `nfsv4`, or weaker, e.g. a TCP-only fallback); null for other types. |
| `warn` | `string` \| `null` | no | Non-fatal warning about a successful result, if any. |

<a id="schema-testscannersrequest"></a>
### TestScannersRequest

Optional scanner_ids subset for POST /test-scanners.

Omit (or send empty) → test against every scanner whose pool /
allowed_source_ids permits this source. Supplying a single id lets
a per-row "Test now" button reuse the same endpoint.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `scanner_ids` | array of `string` (`uuid`) \| `null` | no | Scanners to ask; omit for every eligible online scanner. |

<a id="schema-testscannersresponse"></a>
### TestScannersResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `results` | array of [`TestScannersResultRow`](#schema-testscannersresultrow) | yes | One row per scanner asked. |

<a id="schema-testscannersresultrow"></a>
### TestScannersResultRow

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `completed_at` | `string` (`date-time`) \| `null` | yes | When the probe finished. |
| `error` | `string` \| `null` | yes | Probe error message, if any. |
| `ok` | `boolean` \| `null` | yes | Probe outcome; null while pending. |
| `pending` | `boolean` | yes | True when the scanner did not answer within 5 seconds. |
| `scanner_id` | `string` (`uuid`) \| `null` | yes | Scanner asked. |
| `step` | `string` \| `null` | yes | Probe step that failed, if any. |

<a id="schema-testsharesrequest"></a>
### TestSharesRequest

Optional per-source filter; default = every attached share.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `source_ids` | array of `string` (`uuid`) \| `null` | no | Only test these sources attached to the host; omit to test every attached share. |

<a id="schema-testsharesresponse"></a>
### TestSharesResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `results` | array of [`TestSharesResultRow`](#schema-testsharesresultrow) | yes | One row per (source, eligible scanner). |

<a id="schema-testsharesresultrow"></a>
### TestSharesResultRow

One (source, scanner) probe outcome.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `completed_at` | `string` (`date-time`) \| `null` | yes | When the probe finished; null while pending. |
| `error` | `string` \| `null` | yes | Failure message; null on success or while pending. |
| `ok` | `boolean` \| `null` | yes | Whether the scanner reached the source; null while the probe is pending. |
| `pending` | `boolean` | yes | True when the scanner has not answered yet; the result arrives later on the source-reachability WebSocket channel. |
| `scanner_id` | `string` (`uuid`) | yes | Scanner that ran the probe. |
| `source_id` | `string` (`uuid`) | yes | Source that was probed. |
| `source_name` | `string` | yes | Name of that source. |
| `step` | `string` \| `null` | yes | Step that failed (`connect`, `auth`, `mount`, `list` or `config`); null on success or while pending. |

<a id="schema-testsourcerequest"></a>
### TestSourceRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `connection_config` | `object` | yes | Connection settings for the type, as for source creation. May include `oauth_credential_id` for OAuth-backed types. |
| `credential_profile_id` | `string` (`uuid`) \| `null` | no | Credential profile whose credentials are merged under the inline config. |
| `host_id` | `string` (`uuid`) \| `null` | no | Host whose connection settings and credentials are merged under the inline config. |
| `type` | `string` | yes | Source type, e.g. `local`, `smb`, `nfs`, `s3`, `webdav`, `gdrive`. |

<a id="schema-testsourcesrequest"></a>
### TestSourcesRequest

Optional source_ids subset for POST /scanners/{id}/test-sources.

Omit → test every source the scanner is currently allowed to claim.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `source_ids` | array of `string` (`uuid`) \| `null` | no | Sources to probe; omit to probe every source the scanner may claim. |

<a id="schema-testsourcesresponse"></a>
### TestSourcesResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `results` | array of [`TestSourcesResultRow`](#schema-testsourcesresultrow) | yes | One row per probed source. |

<a id="schema-testsourcesresultrow"></a>
### TestSourcesResultRow

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `completed_at` | `string` (`date-time`) \| `null` | yes | When the probe finished. |
| `error` | `string` \| `null` | yes | Probe error message, if any. |
| `ok` | `boolean` \| `null` | yes | Probe outcome; null while pending. |
| `pending` | `boolean` | yes | True when the scanner did not answer within 5 seconds. |
| `source_id` | `string` (`uuid`) | yes | Probed source. |
| `step` | `string` \| `null` | yes | Probe step that failed, if any. |

<a id="schema-tikaactivity"></a>
### TikaActivity

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `error` | `string` \| `null` | yes | Why the counters could not be read. |
| `extracted_last_5min` | `integer` \| `null` | yes | Files extracted in the last 5 minutes; null if unreadable. |
| `extracted_total` | `integer` \| `null` | yes | Lifetime count of files whose text was extracted; null if unreadable. |
| `last_extracted_at` | `string` \| `null` | yes | ISO-8601 time the latest extraction was reported, as stored. |
| `ok` | `boolean` | yes | False when Redis could not be reached. |

<a id="schema-tokenresponse"></a>
### TokenResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `access_token` | `string` | yes | Short-lived JWT access token; send it as a bearer token. |
| `token_type` | `string` | yes | Always `bearer`. |

<a id="schema-treenode"></a>
### TreeNode

A node of the nested treemap.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `children` | array of [`TreeNode`](#schema-treenode) \| `null` | yes | Child nodes, largest first. |
| `color_key` | `string` \| `null` | yes | Colour bucket key. |
| `id` | `string` (`uuid`) \| `null` | yes | Entry id; absent for synthetic nodes. |
| `kind` | `string` | yes | `file`, `directory` or `other`. |
| `name` | `string` | yes | Base name. |
| `path` | `string` | yes | Full path. |
| `size_bytes` | `integer` | yes | Node size in bytes. |

<a id="schema-unboundidentityout"></a>
### UnboundIdentityOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `confidence` | `string` | yes | How the claim was obtained, one of `claim`, `ldap`, `smb`, `name`. |
| `first_seen_at` | `string` (`date-time`) | yes | When the claim was first seen at login. |
| `groups` | array of `string` | yes | Group identifiers that came with the claim. |
| `id` | `string` (`uuid`) | yes | Claim id. |
| `identifier` | `string` | yes | The identifier value, e.g. a SID. |
| `identity_type` | `string` | yes | Kind of identifier: `posix_uid`, `sid`, `nfsv4_principal` or `s3_canonical`. |
| `last_seen_at` | `string` (`date-time`) | yes | When it was last seen. |
| `user_id` | `string` (`uuid`) | yes | User the claim was seen for. |

<a id="schema-usercreate"></a>
### UserCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` \| `null` | no | Optional contact address. |
| `password` | `string` | yes | Password, 8-128 characters and at most 72 UTF-8 bytes (bcrypt's limit). |
| `role` | `"viewer"` \| `"admin"` | no | Role of the new account: `viewer` (default) or `admin`. |
| `username` | `string` | yes | Login name, 3-64 characters. |

<a id="schema-userlogin"></a>
### UserLogin

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `password` | `string` | yes | Account password. |
| `username` | `string` | yes | Login name. |

<a id="schema-userresponse"></a>
### UserResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `auth_provider` | `string` | yes | Where the account authenticates: `local`, `oidc` or `ldap`. |
| `created_at` | `string` (`date-time`) | yes | When the account was created. |
| `email` | `string` \| `null` | yes | Contact address, if set. |
| `id` | `string` (`uuid`) | yes | User id. |
| `last_login_at` | `string` (`date-time`) \| `null` | yes | Last successful login, if any. |
| `role` | `"viewer"` \| `"admin"` | yes | Role bundle: `admin` or `viewer`. |
| `role_pinned` | `boolean` | yes | True when an admin set the role by hand, so IdP group mapping leaves it alone. |
| `username` | `string` | yes | Login name. |

<a id="schema-userroleupdate"></a>
### UserRoleUpdate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `role` | `"viewer"` \| `"admin"` \| `null` | no | New role. Setting it pins the user unless `role_pinned` is also given. |
| `role_pinned` | `boolean` \| `null` | no | Pin the role (true) or hand it back to IdP group mapping (false). |

<a id="schema-watchdogout"></a>
### WatchdogOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `active_after` | `integer` | yes | Active scans after the pass. |
| `active_before` | `integer` | yes | Active scans before the pass. |

<a id="schema-webhookcreate"></a>
### WebhookCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `event_type` | `string` | yes | Event to subscribe to, e.g. `scan.completed` or `scan.failed`. |
| `secret` | `string` | yes | Shared secret used to sign deliveries (hex HMAC-SHA256 of the raw request body in `X-Akashic-Signature`). Write-only: never returned. |
| `url` | `string` | yes | HTTPS or HTTP endpoint that receives the POST. Must resolve to a public address. |

<a id="schema-webhookresponse"></a>
### WebhookResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` (`date-time`) | yes | When the webhook was created. |
| `enabled` | `boolean` | yes | Whether deliveries are sent. |
| `event_type` | `string` | yes | Event the webhook subscribes to. |
| `id` | `string` (`uuid`) | yes | Webhook id. |
| `url` | `string` | yes | Delivery endpoint. |

<a id="schema-workunitout"></a>
### WorkUnitOut

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` (`uuid`) | yes | Work unit id. |
| `lease_expires_at` | `string` (`date-time`) \| `null` | yes | When the lease lapses unless renewed with a heartbeat. |
| `path` | `string` | yes | Directory subtree to walk, relative to the source root; empty for the root. |
| `scan_id` | `string` (`uuid`) | yes | Scan the unit belongs to. |
| `status` | `string` | yes | Unit status; `running` while leased. |

<a id="schema-wserror"></a>
### WsError

Live stream failed; clients should fall back to polling.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `"error"` | yes | Frame discriminator. |
| `message` | `string` | yes | Why the live stream is unavailable. |

<a id="schema-wshostchanged"></a>
### WsHostChanged

A host was created, edited or deleted; refetch hosts (`/ws/v1/scans`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `host_id` | `string` | yes | Host id. |
| `kind` | `"host.changed"` | yes | Frame discriminator. |

<a id="schema-wspendingdiscovery"></a>
### WsPendingDiscovery

A scanner discovery request awaiting approval.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `agent_version` | `string` \| `null` | yes | Scanner version. |
| `expires_at` | `string` | yes | ISO-8601 expiry time. |
| `hostname` | `string` \| `null` | yes | Scanner host name. |
| `id` | `string` | yes | Discovery request id. |
| `key_fingerprint` | `string` | yes | Fingerprint of the scanner's public key. |
| `pairing_code` | `string` | yes | Code the operator matches against the scanner's console. |
| `requested_at` | `string` | yes | ISO-8601 request time. |
| `requested_pool` | `string` \| `null` | yes | Pool the scanner asked to join. |

<a id="schema-wsping"></a>
### WsPing

Server keep-alive frame.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `"ping"` | yes | Frame discriminator. |

<a id="schema-wsscanlistentry"></a>
### WsScanListEntry

One active scan in the `/ws/v1/scans` snapshot.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `current_path` | `string` \| `null` | yes | Path currently being walked. |
| `files_found` | `integer` | yes | Files found so far. |
| `scan_id` | `string` | yes | Scan id. |
| `scan_status` | `"pending"` \| `"running"` \| `"completed"` \| `"failed"` \| `"cancelled"` | yes | Scan status. |
| `scan_type` | `string` | yes | Scan type (`incremental` or `full`). |
| `scanner_id` | `string` \| `null` | yes | Assigned scanner id. |
| `scanner_name` | `string` \| `null` | yes | Assigned scanner name. |
| `source_id` | `string` | yes | Source id. |
| `source_status` | `string` | yes | Source status. |
| `started_at` | `string` \| `null` | yes | ISO-8601 start time. |

<a id="schema-wsscanlistsnapshot"></a>
### WsScanListSnapshot

First frame on `/ws/v1/scans`: the pending, running and failed scans the caller can see.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `"snapshot"` | yes | Frame discriminator. |
| `scans` | array of [`WsScanListEntry`](#schema-wsscanlistentry) | yes | Pending, running and recently failed scans. |

<a id="schema-wsscanlog"></a>
### WsScanLog

New log (or stderr) lines on `/ws/v1/scans/{id}`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `"log"` \| `"stderr"` | yes | Frame discriminator. |
| `lines` | array of [`WsScanLogLine`](#schema-wsscanlogline) | yes | Newly persisted lines. |
| `scan_id` | `string` | yes | Scan id. |

<a id="schema-wsscanlogline"></a>
### WsScanLogLine

One scan log line.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes | Log entry id. |
| `level` | `string` | yes | Log level, or `stderr` for scanner stderr chunks. |
| `message` | `string` | yes | Log text. |
| `scanner_id` | `string` \| `null` | yes | Scanner that produced the line. |
| `scanner_name` | `string` \| `null` | yes | Scanner name snapshotted at write time. |
| `ts` | `string` | yes | ISO-8601 timestamp. |

<a id="schema-wsscanprogress"></a>
### WsScanProgress

Throttled progress frame on `/ws/v1/scans/{id}`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `bytes_scanned` | `integer` | yes | Bytes scanned so far. |
| `current_batch_size` | `integer` \| `null` | yes | Latest adaptive batch size. |
| `current_path` | `string` \| `null` | yes | Path currently being walked. |
| `dirs_queued` | `integer` | yes | Directories queued. |
| `dirs_walked` | `integer` | yes | Directories walked. |
| `files_scanned` | `integer` | yes | Files scanned so far. |
| `files_skipped` | `integer` | yes | Files skipped. |
| `kind` | `"progress"` | yes | Frame discriminator. |
| `phase` | `string` \| `null` | yes | Current scan phase. |
| `scan_id` | `string` | yes | Scan id. |
| `total_estimated` | `integer` \| `null` | yes | Estimated total files, if known. |
| `ts` | `string` | yes | ISO-8601 timestamp. |

<a id="schema-wsscansnapshot"></a>
### WsScanSnapshot

First frame on `/ws/v1/scans/{id}`: current scan state plus recent log lines.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `bytes_scanned_so_far` | `integer` | yes | Bytes scanned so far. |
| `completed_at` | `string` \| `null` | yes | ISO-8601 completion time. |
| `current_batch_size` | `integer` \| `null` | yes | Latest adaptive batch size. |
| `current_path` | `string` \| `null` | yes | Path currently being walked. |
| `dirs_queued` | `integer` | yes | Directories queued. |
| `dirs_walked` | `integer` | yes | Directories walked. |
| `error_message` | `string` \| `null` | yes | Failure reason, if any. |
| `files_changed` | `integer` | yes | Files changed since the previous scan. |
| `files_deleted` | `integer` | yes | Files deleted since the previous scan. |
| `files_found` | `integer` | yes | Files found so far. |
| `files_new` | `integer` | yes | Files new since the previous scan. |
| `files_skipped` | `integer` | yes | Files skipped. |
| `kind` | `"snapshot"` | yes | Frame discriminator. |
| `last_heartbeat_at` | `string` \| `null` | yes | ISO-8601 time of the last scanner heartbeat. |
| `phase` | `string` \| `null` | yes | Current scan phase. |
| `previous_scan_files` | `integer` \| `null` | yes | File count of the previous scan. |
| `recent_lines` | array of [`WsScanLogLine`](#schema-wsscanlogline) | yes | Most recent log lines, oldest first. |
| `scan_id` | `string` | yes | Scan id. |
| `source_id` | `string` | yes | Source being scanned. |
| `started_at` | `string` \| `null` | yes | ISO-8601 start time. |
| `status` | `string` | yes | Scan status. |
| `total_estimated` | `integer` \| `null` | yes | Estimated total files, if known. |

<a id="schema-wsscanstate"></a>
### WsScanState

Scan state change on `/ws/v1/scans` (the source-list stream).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `current_batch_size` | `integer` \| `null` | yes | Latest adaptive batch size, when the scanner reported one. |
| `current_path` | `string` \| `null` | yes | Path currently being walked. |
| `files_found` | `integer` | yes | Files found so far. |
| `kind` | `"scan.state"` | yes | Frame discriminator. |
| `scan_id` | `string` | yes | Scan id. |
| `scan_status` | `"pending"` \| `"running"` \| `"completed"` \| `"failed"` \| `"cancelled"` | yes | Scan status. |
| `scan_type` | `string` | yes | Scan type (`incremental` or `full`). |
| `scanner_id` | `string` \| `null` | yes | Assigned scanner id. |
| `scanner_name` | `string` \| `null` | yes | Assigned scanner name. |
| `source_id` | `string` | yes | Source id. |
| `source_status` | `string` | yes | Source status. |
| `started_at` | `string` \| `null` | yes | ISO-8601 start time; null until a scanner has started the scan. |

<a id="schema-wsscannerclaimredeemed"></a>
### WsScannerClaimRedeemed

A claim token was redeemed and a scanner registered (`/ws/v1/scanners`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `"scanner.claim_redeemed"` | yes | Frame discriminator. |
| `pool` | `string` | yes | Scanner pool. |
| `scanner_id` | `string` | yes | New scanner id. |
| `scanner_name` | `string` | yes | Scanner name. |
| `token_id` | `string` | yes | Claim token id. |

<a id="schema-wsscannerdeleted"></a>
### WsScannerDeleted

A scanner was removed (`/ws/v1/scanners`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `"scanner.deleted"` | yes | Frame discriminator. |
| `scanner_id` | `string` | yes | Scanner id. |

<a id="schema-wsscannerdiscoveryapproved"></a>
### WsScannerDiscoveryApproved

A discovery request was approved (`/ws/v1/scanners`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `discovery_id` | `string` | yes | Discovery request id. |
| `kind` | `"scanner.discovery_approved"` | yes | Frame discriminator. |
| `pool` | `string` | yes | Scanner pool. |
| `scanner_id` | `string` | yes | New scanner id. |
| `scanner_name` | `string` | yes | Scanner name. |

<a id="schema-wsscannerdiscoverydenied"></a>
### WsScannerDiscoveryDenied

A discovery request was denied (`/ws/v1/scanners`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `deny_reason` | `string` \| `null` | yes | Operator-supplied reason. |
| `discovery_id` | `string` | yes | Discovery request id. |
| `kind` | `"scanner.discovery_denied"` | yes | Frame discriminator. |

<a id="schema-wsscannerdiscoveryexpired"></a>
### WsScannerDiscoveryExpired

A discovery request expired unanswered (`/ws/v1/scanners`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `discovery_id` | `string` | yes | Discovery request id. |
| `kind` | `"scanner.discovery_expired"` | yes | Frame discriminator. |

<a id="schema-wsscannerdiscoveryrequested"></a>
### WsScannerDiscoveryRequested

A scanner asked to be approved (`/ws/v1/scanners`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `agent_version` | `string` \| `null` | yes | Scanner version. |
| `discovery_id` | `string` | yes | Discovery request id. |
| `expires_at` | `string` | yes | ISO-8601 expiry time. |
| `hostname` | `string` \| `null` | yes | Scanner host name. |
| `key_fingerprint` | `string` | yes | Fingerprint of the scanner's public key. |
| `kind` | `"scanner.discovery_requested"` | yes | Frame discriminator. |
| `pairing_code` | `string` | yes | Pairing code. |
| `requested_pool` | `string` \| `null` | yes | Requested pool. |

<a id="schema-wsscannerupdated"></a>
### WsScannerUpdated

A scanner's settings or reported metadata changed; refetch it (`/ws/v1/scanners`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `"scanner.updated"` | yes | Frame discriminator. |
| `scanner_id` | `string` | yes | Scanner id. |

<a id="schema-wsscannerssnapshot"></a>
### WsScannersSnapshot

First frame on `/ws/v1/scanners`: pending discovery requests.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `"snapshot"` | yes | Frame discriminator. |
| `pending_discoveries` | array of [`WsPendingDiscovery`](#schema-wspendingdiscovery) | yes | Discovery requests awaiting approval, newest first. |

<a id="schema-wssourcecreated"></a>
### WsSourceCreated

A source was created (`/ws/v1/scans`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `"source.created"` | yes | Frame discriminator. |
| `name` | `string` | yes | Source name. |
| `source_id` | `string` | yes | Source id. |
| `source_status` | `string` | yes | Source status. |
| `type` | `string` | yes | Source type. |

<a id="schema-wssourcedeleted"></a>
### WsSourceDeleted

A source was deleted (`/ws/v1/scans`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `"source.deleted"` | yes | Frame discriminator. |
| `source_id` | `string` | yes | Source id. |

<a id="schema-wssourceupdated"></a>
### WsSourceUpdated

A source was edited; refetch it (`/ws/v1/scans`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `"source.updated"` | yes | Frame discriminator. |
| `source_id` | `string` | yes | Source id. |

<a id="schema--bulkapplybody"></a>
### _BulkApplyBody

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `entry_ids` | array of `string` (`uuid`) | yes | Entries to tag (at least one). |
| `tags` | array of `string` | yes | Tag names to apply to each entry (at least one). |

<a id="schema--entrysourceref"></a>
### _EntrySourceRef

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` (`uuid`) | yes | Source id. |
| `name` | `string` | yes | Source name. |
| `security_metadata` | [`SourceSecurityMetadata-Output`](#schema-sourcesecuritymetadata-output) \| `null` | yes | Source-level security data used when evaluating permissions (S3 sources only); null when none. |
| `type` | `string` | yes | Source type, e.g. `local`, `smb`, `nfs`, `s3`. |

<a id="schema--hostinline"></a>
### _HostInline

Inline shape carried on a SourceResponse — name + type only.

Full host details (including masked credentials) live behind
GET /api/v1/hosts/{id}. Inlining the credentials on every source
response would be both heavy and a needless surface area.

`credential_profile_id` is included (v0.31.4) so the source detail
panel can show that a host-backed source inherits its credentials
from the host's profile — the source's own `credential_profile_id`
stays NULL by design. It is an id, not a secret, so it is safe to
inline.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `credential_profile_id` | `string` (`uuid`) \| `null` | yes | Credential profile the host uses, if any. |
| `id` | `string` (`uuid`) | yes | Host id. |
| `name` | `string` | yes | Host name. |
| `type` | `string` | yes | Host type: `smb`, `nfs` or `s3`. |

<a id="schema--tagapplybody"></a>
### _TagApplyBody

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `tags` | array of `string` | yes | Tag names to apply (at least one). |
