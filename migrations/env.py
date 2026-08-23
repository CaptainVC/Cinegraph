from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from cinegraph.adapters.identity.sqlalchemy_identity_repositories import (  # noqa: F401
    SessionEntitlementRow,
    SessionRow,
    UserAccountRow,
)
from cinegraph.adapters.persistence.base import PersistenceBase
from cinegraph.adapters.persistence.sqlalchemy_graph_claim_store import (  # noqa: F401
    GraphClaimEvidenceRow,
    GraphClaimRow,
    GraphEntityAliasRow,
    GraphEntityRow,
)
from cinegraph.adapters.persistence.sqlalchemy_ingestion_job_repository import (  # noqa: F401
    IngestionJobEventRow,
    IngestionJobRow,
)
from cinegraph.config import CinegraphRuntimeSettings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = PersistenceBase.metadata


def _database_url() -> str:
    configured = config.attributes.get("database_url")
    if configured is not None:
        return str(configured)
    settings = CinegraphRuntimeSettings()
    if settings.database_url is None:
        raise RuntimeError("Database URL is not configured.")
    return settings.database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
