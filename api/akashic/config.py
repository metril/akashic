"""Bootstrap settings: only what is needed before the DB is reachable.

Everything else is runtime-tunable and lives in the DB-backed registry
(``akashic.services.runtime_settings``), edited from the web UI.
"""
import logging
import os
import secrets
import tempfile
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_LEGACY_DEFAULT_SECRET_KEY = "changeme-secret-key"


def _read_key(path: Path) -> str | None:
    try:
        v = path.read_text().strip()
    except FileNotFoundError:
        return None
    return v or None


def load_or_create_secret_key(path: str) -> str:
    """Return the persisted signing/encryption key, creating it (0600) once.

    Order: existing file -> legacy SECRET_KEY env (persisted so data it
    already encrypted stays readable) -> freshly generated key. Creation
    is atomic (temp file + hard link) so concurrent workers agree.
    """
    p = Path(path)
    existing = _read_key(p)
    if existing:
        return existing
    legacy = (os.environ.get("SECRET_KEY") or "").strip()
    if legacy and legacy != _LEGACY_DEFAULT_SECRET_KEY:
        new, source = legacy, "legacy SECRET_KEY env"
    else:
        new, source = secrets.token_urlsafe(64), "generated"
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".secret-")
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, new.encode())
        os.close(fd)
        try:
            os.link(tmp, p)
            logger.warning("secret key created at %s (%s)", p, source)
        except FileExistsError:
            pass
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    return _read_key(p) or new


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic"
    meili_url: str = "http://localhost:7700"
    meili_key: str = "changeme-meili-key"
    redis_url: str = "redis://localhost:6379/0"
    secret_key_file: str = "/data/secret.key"
    # Resolved from secret_key_file at construction. The alias is a name no
    # environment variable can match, so a legacy SECRET_KEY env var is never
    # read straight into the field; load_or_create_secret_key reads (and
    # persists) it explicitly instead.
    secret_key: str = Field(default="", validation_alias="_akashic_secret_key_unset")

    @model_validator(mode="after")
    def _resolve_secret_key(self) -> "Settings":
        self.secret_key = load_or_create_secret_key(self.secret_key_file)
        return self

    model_config = {"env_prefix": "", "case_sensitive": False, "extra": "ignore"}


settings = Settings()
