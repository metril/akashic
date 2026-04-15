from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic"
    meili_url: str = "http://localhost:7700"
    meili_key: str = "changeme-meili-key"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "changeme-secret-key"
    access_token_expire_minutes: int = 60
    tika_url: str = "http://localhost:9998"

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
