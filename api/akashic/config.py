from pydantic import field_validator
from pydantic_settings import BaseSettings


# Default values that ship in source — used here as a deny-list so a
# deployment that forgot to set the corresponding env var fails fast
# at startup instead of running with a known key. The access-token
# JWT and the OAuth-credential Fernet derivation both depend on
# secret_key; using the shipped default would let anyone forge tokens
# or trial-decrypt every persisted refresh token.
_DEFAULT_SECRET_KEY = "changeme-secret-key"


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic"
    meili_url: str = "http://localhost:7700"
    meili_key: str = "changeme-meili-key"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = _DEFAULT_SECRET_KEY

    @field_validator("secret_key")
    @classmethod
    def _reject_default_secret_key(cls, v: str) -> str:
        # Allow the placeholder during tests and local dev where the env
        # var is intentionally unset; gate by AKASHIC_DEV_ALLOW_DEFAULT_KEY=1
        # so production startup blows up cleanly instead.
        import os
        if v == _DEFAULT_SECRET_KEY and not os.environ.get(
            "AKASHIC_DEV_ALLOW_DEFAULT_KEY"
        ):
            raise ValueError(
                "SECRET_KEY is set to the shipped default. "
                "Set the SECRET_KEY env var to a long random value, or "
                "set AKASHIC_DEV_ALLOW_DEFAULT_KEY=1 for local-dev only.",
            )
        if not v.strip():
            raise ValueError("SECRET_KEY must be non-empty")
        return v
    access_token_expire_minutes: int = 60
    # Phase 8 — refresh tokens. Default 30 days matches typical SSO
    # session lifetimes; deployments wanting shorter sessions tighten
    # this without touching the access-token TTL above.
    refresh_token_expire_days: int = 30
    tika_url: str = "http://localhost:9998"

    # Recover scans/sources stuck in pending|running|scanning after this many minutes.
    stale_scan_threshold_minutes: int = 60

    # v0.5.6 — periodic reachability checks. The scheduler enqueues a
    # ReachabilityCheck row per source whose last_reachability_check_at
    # is older than `reachability_check_interval_seconds`. Scanner
    # agents and the api self-worker claim rows via SELECT FOR UPDATE
    # SKIP LOCKED, run `test-connection`, and report back. The
    # staleness threshold the UI uses is 2× this interval.
    reachability_check_enabled: bool = True
    reachability_check_interval_seconds: int = 300
    reachability_check_max_concurrency: int = 4

    audit_retention_days: int = 0  # 0 = forever

    # Cookie hardening. Default True so production deployments behind
    # TLS get the Secure flag without extra config. Local dev that
    # serves http:// (no TLS) must explicitly set COOKIE_SECURE=false
    # to actually receive the cookies, since browsers drop Secure
    # cookies on plain HTTP. (Review A-I1, A-N3.)
    cookie_secure: bool = True

    # CORS — explicit allow-list for cross-origin browser fetches.
    # Empty default means same-origin only (no CORSMiddleware mounted).
    # Set CORS_ALLOW_ORIGINS=["https://akashic.example.com"] (JSON list
    # in env) to enable. (Review A-I4.)
    cors_allow_origins: list[str] = []

    # Slow-query observability (v0.4.x). Any SQL statement whose
    # execution exceeds the matching threshold gets logged at WARN.
    # `slow_query_ms` is the global default; `slow_query_ms_overrides`
    # lets ops tighten it for specific endpoints (the prefix matches
    # against the request path) so Browse/Search regressions surface
    # before they reach 100 ms while ingest's intentionally heavier
    # batch endpoint can have a roomier ceiling.
    #
    # Set via env as JSON: SLOW_QUERY_MS_OVERRIDES='{"browse": 50, "ingest": 200}'
    slow_query_ms: int = 100
    slow_query_ms_overrides: dict[str, int] = {}

    # First-boot seed for the runtime `discovery_enabled` server
    # setting. Once the row exists, runtime PATCHes from the UI take
    # precedence over this env var. Set to true in IaC if you want
    # discovery on by default; the operator can still flip it at
    # runtime without changing config.
    scanner_discovery_enabled: bool | None = None

    # OIDC
    oidc_enabled: bool = False
    oidc_discovery_url: str = ""  # e.g. https://auth.example.com/.well-known/openid-configuration
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:8000/api/auth/oidc/callback"

    # OIDC → FsBinding bridge (Phase 2a). See docs/oidc-authentik.md.
    # `auto` tries claim → ldap_fallback → name_match in that order; pin to a
    # specific strategy for predictable behaviour. claim/ldap_fallback only
    # apply when an AD-style SID actually exists; name_match is the
    # last-resort POSIX-friendly path.
    oidc_strategy: str = "auto"  # auto | claim | ldap_fallback | name_match
    oidc_username_claim: str = "preferred_username"
    oidc_email_claim: str = "email"
    oidc_sid_claim: str = "onprem_sid"  # Authentik AD federation default after the mapper.
    oidc_uid_claim: str = "uidNumber"  # POSIX UID, when the IdP federates LDAP/POSIX.
    oidc_groups_claim: str = "groups"
    oidc_groups_format: str = "sid"  # sid | name | dn
    oidc_dn_claim: str = "ldap_dn"  # used by ldap_fallback to seed an AD bind

    group_cache_ttl_hours: int = 24

    # Phase 5 — when on, Browse and entry-by-id apply the same per-user
    # ACL filter Search has always applied. Defaults off so existing
    # deployments don't suddenly hide files from existing users on
    # upgrade; flip to true once the deployment has FsBindings set up
    # (and ideally has run the Phase-4 backfill on existing entries).
    browse_enforce_perms: bool = False

    # v0.4.11 Phase 8e — when on, ingest batches mark touched parent
    # paths in a Redis dirty set; a single background worker drains
    # the set and recomputes top_children incrementally so the
    # storage explorer sees fresh data mid-scan rather than waiting
    # for the post-scan rollup. Off by default — the post-scan rollup
    # is enough for most installs and the worker adds a moving part
    # to ingest. Flip on for installs that scan very large estates
    # and care about live storage-tree freshness.
    streaming_topchildren: bool = False

    # LDAP
    ldap_enabled: bool = False
    ldap_server: str = ""  # e.g. ldap://ldap.example.com:389
    ldap_bind_dn: str = ""  # e.g. cn=admin,dc=example,dc=com
    ldap_bind_password: str = ""
    ldap_user_base: str = ""  # e.g. ou=users,dc=example,dc=com
    ldap_user_filter: str = "(uid={username})"

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
