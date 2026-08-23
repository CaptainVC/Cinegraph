from pathlib import Path

import pytest
from pydantic import ValidationError

from cinegraph.common.error_messages import ConfigurationErrorMessages
from cinegraph.config import (
    CinegraphRuntimeSettings,
    QdrantRuntimeMode,
    RuntimeEnvironment,
)


def test_development_defaults_to_private_local_qdrant() -> None:
    settings = CinegraphRuntimeSettings(_env_file=None)

    assert settings.environment is RuntimeEnvironment.DEVELOPMENT
    assert settings.qdrant_mode is QdrantRuntimeMode.LOCAL
    assert settings.qdrant_local_path == Path("knowledge/qdrant-development")
    assert settings.identity_database_path == Path(
        "knowledge/cinegraph-development.sqlite3"
    )
    assert settings.qdrant_collection_name == "transcript_segments_development"
    assert settings.qdrant_api_key is None


def test_remote_settings_load_from_prefixed_environment_file(tmp_path: Path) -> None:
    env_file = tmp_path / "runtime.env"
    env_file.write_text(
        "\n".join(
            (
                "CINEGRAPH_ENVIRONMENT=production",
                "CINEGRAPH_QDRANT_MODE=remote",
                "CINEGRAPH_QDRANT_URL=https://qdrant.example.test",
                "CINEGRAPH_QDRANT_API_KEY=private-value",
                "CINEGRAPH_QDRANT_COLLECTION_NAME=transcript_segments_production",
                "CINEGRAPH_DATABASE_URL=postgresql+psycopg://cinegraph:secret@db.example.test/cinegraph",
            )
        ),
        encoding="utf-8",
    )

    settings = CinegraphRuntimeSettings(_env_file=env_file)

    assert settings.environment is RuntimeEnvironment.PRODUCTION
    assert settings.qdrant_mode is QdrantRuntimeMode.REMOTE
    assert str(settings.qdrant_url) == "https://qdrant.example.test/"
    assert settings.qdrant_api_key.get_secret_value() == "private-value"
    assert "private-value" not in repr(settings)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (
            {"qdrant_mode": QdrantRuntimeMode.LOCAL, "qdrant_local_path": None},
            ConfigurationErrorMessages.QDRANT_LOCAL_PATH_REQUIRED,
        ),
        (
            {"qdrant_mode": QdrantRuntimeMode.REMOTE, "qdrant_url": None},
            ConfigurationErrorMessages.QDRANT_REMOTE_URL_REQUIRED,
        ),
        (
            {
                "environment": RuntimeEnvironment.PRODUCTION,
                "qdrant_mode": QdrantRuntimeMode.LOCAL,
            },
            ConfigurationErrorMessages.PRODUCTION_QDRANT_MUST_BE_REMOTE,
        ),
        (
            {
                "environment": RuntimeEnvironment.PRODUCTION,
                "qdrant_mode": QdrantRuntimeMode.REMOTE,
                "qdrant_url": "https://qdrant.example.test",
            },
            ConfigurationErrorMessages.PRODUCTION_DATABASE_MUST_BE_POSTGRES,
        ),
    ],
)
def test_incompatible_runtime_settings_fail_closed(values: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        CinegraphRuntimeSettings(_env_file=None, **values)


def test_development_rejects_unapproved_database_driver() -> None:
    with pytest.raises(
        ValidationError,
        match=ConfigurationErrorMessages.DATABASE_DIALECT_MUST_BE_SUPPORTED,
    ):
        CinegraphRuntimeSettings(
            _env_file=None,
            database_url="mysql+pymysql://cinegraph@example.test/cinegraph",
        )
