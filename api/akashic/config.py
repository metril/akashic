from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic"
    meili_url: str = "http://localhost:7700"
    meili_key: str = "changeme-meili-key"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "changeme-secret-key"
    access_token_expire_minutes: int = 60
    tika_url: str = "http://localhost:9998"

    # OIDC
    oidc_enabled: bool = False
    oidc_discovery_url: str = ""  # e.g. https://auth.example.com/.well-known/openid-configuration
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:8000/api/auth/oidc/callback"

    # LDAP
    ldap_enabled: bool = False
    ldap_server: str = ""  # e.g. ldap://ldap.example.com:389
    ldap_bind_dn: str = ""  # e.g. cn=admin,dc=example,dc=com
    ldap_bind_password: str = ""
    ldap_user_base: str = ""  # e.g. ou=users,dc=example,dc=com
    ldap_user_filter: str = "(uid={username})"

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
