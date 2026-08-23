from typing import Protocol

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url

from cinegraph.common.error_messages import ConfigurationErrorMessages
from cinegraph.config import DEFAULT_DATABASE_CONFIGURATION, CinegraphRuntimeSettings


class _DbapiCursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def close(self) -> None: ...


class _DbapiConnection(Protocol):
    def cursor(self) -> _DbapiCursor: ...


def create_database_engine(settings: CinegraphRuntimeSettings) -> Engine:
    if settings.database_url is None:
        raise ValueError(ConfigurationErrorMessages.DATABASE_URL_MUST_BE_CONFIGURED)
    url = make_url(settings.database_url.get_secret_value())
    is_sqlite = url.get_backend_name() == DEFAULT_DATABASE_CONFIGURATION.sqlite_backend_name
    connect_args: dict[str, object] = {}
    if is_sqlite:
        connect_args["check_same_thread"] = False
    engine_options: dict[str, object] = {"connect_args": connect_args, "pool_pre_ping": True}
    if not is_sqlite:
        engine_options.update(
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_recycle=settings.database_pool_recycle_seconds,
        )
    engine = create_engine(url, **engine_options)
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: _DbapiConnection, _: object) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    return engine
