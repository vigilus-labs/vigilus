"""Alembic environment configuration for async SQLAlchemy migrations."""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# Import all models so Alembic sees them
from vigilus.db.base import Base  # noqa: E402
from vigilus.db import models as _models  # noqa: E402, F401

# Alembic Config object
config = context.config

# Set up loggers from the config file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# MetaData for autogenerate
target_metadata = Base.metadata

# Override sqlalchemy.url from environment if available
db_url = os.environ.get("VIGILUS_DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Generates SQL scripts without connecting to the database.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _is_autogenerate() -> bool:
    cmd_opts = getattr(config, "cmd_opts", None)
    return cmd_opts is not None and getattr(cmd_opts, "autogenerate", False)


def do_run_migrations(connection) -> None:
    """Run migrations with an active connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    # Adopt pre-Alembic databases: init_db()'s create_all builds the whole
    # schema from the current models — the very schema head describes — but
    # records no alembic_version row. Replaying the chain against it fails on
    # the first duplicate column, so stamp head instead of migrating. Never
    # diverts autogenerate, which needs run_migrations() to emit the diff.
    migration_ctx = context.get_context()
    if migration_ctx.get_current_revision() is None and not _is_autogenerate():
        from alembic.script import ScriptDirectory

        migration_ctx.stamp(ScriptDirectory.from_config(config), "head")
        # For SQLite (non-transactional DDL) context.begin_transaction() is a
        # nullcontext, so the version-row INSERT sits in the connection's
        # autobegin transaction — commit it or it rolls back on close.
        connection.commit()
        print("Existing schema adopted: stamped at head, no migrations replayed.")
        return

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine."""
    configuration = config.get_section(config.config_ini_section, {})

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with an async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
