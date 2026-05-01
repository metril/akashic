from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic"
    meili_url: str = "http://localhost:7700"
    meili_key: str = "changeme-meili-key"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "changeme-secret-key"
    access_token_expire_minutes: int = 60
    tika_url: str = "http://localhost:9998"

    # Recover scans/sources stuck in pending|running|scanning after this many minutes.
    stale_scan_threshold_minutes: int = 60

    audit_retention_days: int = 0  # 0 = forever

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

    # LDAP
    ldap_enabled: bool = False
    ldap_server: str = ""  # e.g. ldap://ldap.example.com:389
    ldap_bind_dn: str = ""  # e.g. cn=admin,dc=example,dc=com
    ldap_bind_password: str = ""
    ldap_user_base: str = ""  # e.g. ou=users,dc=example,dc=com
    ldap_user_filter: str = "(uid={username})"

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
