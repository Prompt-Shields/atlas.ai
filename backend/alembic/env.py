"""Alembic environment configuration for async migrations."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import get_settings
from app.database import Base
from app.models import *  # noqa: F401, F403 — import all models for autogenerate

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    settings = get_settings()
    return settings.database_url


def run_migrations_offline() -> None:
    """Run migrations in offline mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema="grc",
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    # `version_table_schema="grc"` means alembic writes its own bookkeeping
    # table into grc, which it does *before* running 001 — so the schemas have
    # to exist first or `upgrade head` dies on an empty database with
    # "schema grc does not exist". 001 creates them too; this makes
    # `alembic upgrade head` self-sufficient wherever it runs.
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS grc"))
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS audit"))
    # Commit here, and not only for durability: executing on the connection
    # opens an implicit transaction, and leaving it open turns alembic's own
    # `begin_transaction()` into a nested no-op whose commit never reaches the
    # database — every migration then silently rolls back at close.
    connection.commit()

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table_schema="grc",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
