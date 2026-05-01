import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from akashic.config import settings
from akashic.database import Base
from akashic.models import *  # noqa: F401,F403

# We deliberately do NOT call logging.config.fileConfig here. Alembic's
# default boilerplate calls fileConfig(config.config_file_name) which
# globally clobbers Python's logging configuration based on alembic.ini's
# [loggers] section — fine for the standalone CLI, but disastrous when
# the api invokes alembic in-process from its lifespan or from pytest.
# Tests using caplog stop seeing log records once env.py has reconfigured
# the root logger, and uvicorn loses its log formatting at startup.
# Logging is the caller's responsibility.

config = context.config

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = settings.database_url
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(settings.database_url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
