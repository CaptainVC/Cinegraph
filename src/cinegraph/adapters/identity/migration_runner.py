from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from cinegraph.common.error_messages import ConfigurationErrorMessages
from cinegraph.config import DEFAULT_DATABASE_CONFIGURATION, CinegraphRuntimeSettings


def upgrade_identity_database(settings: CinegraphRuntimeSettings) -> None:
    """Apply checked-in identity migrations; never creates schema implicitly."""
    if settings.database_url is None:
        raise ValueError(ConfigurationErrorMessages.DATABASE_URL_MUST_BE_CONFIGURED)
    _ensure_sqlite_parent(settings.database_url.get_secret_value())
    project_root = Path(__file__).resolve().parents[4]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.attributes["database_url"] = settings.database_url.get_secret_value()
    command.upgrade(alembic_config, "head")


def downgrade_identity_database(settings: CinegraphRuntimeSettings, revision: str = "-1") -> None:
    if settings.database_url is None:
        raise ValueError(ConfigurationErrorMessages.DATABASE_URL_MUST_BE_CONFIGURED)
    project_root = Path(__file__).resolve().parents[4]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.attributes["database_url"] = settings.database_url.get_secret_value()
    command.downgrade(alembic_config, revision)


def _ensure_sqlite_parent(database_url: str) -> None:
    parsed = make_url(database_url)
    database_path = parsed.database
    if (
        parsed.get_backend_name()
        != DEFAULT_DATABASE_CONFIGURATION.sqlite_backend_name
        or database_path is None
        or database_path == ":memory:"
    ):
        return
    Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
