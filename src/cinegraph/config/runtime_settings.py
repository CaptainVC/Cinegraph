from enum import StrEnum
from pathlib import Path

from pydantic import AnyHttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cinegraph.common.error_messages import ConfigurationErrorMessages


class RuntimeEnvironment(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class QdrantRuntimeMode(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"


class CinegraphRuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CINEGRAPH_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    knowledge_root: Path = Path("knowledge")
    identity_database_path: Path = Path("knowledge/cinegraph-development.sqlite3")
    qdrant_mode: QdrantRuntimeMode = QdrantRuntimeMode.LOCAL
    qdrant_local_path: Path | None = Path("knowledge/qdrant-development")
    qdrant_url: AnyHttpUrl | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_collection_name: str = "transcript_segments_development"

    @field_validator("qdrant_collection_name")
    @classmethod
    def require_trimmed_collection_name(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError(
                ConfigurationErrorMessages.QDRANT_COLLECTION_NAME_MUST_BE_TRIMMED
            )
        return value

    @model_validator(mode="after")
    def require_environment_compatible_qdrant(self) -> "CinegraphRuntimeSettings":
        if self.qdrant_mode is QdrantRuntimeMode.LOCAL and self.qdrant_local_path is None:
            raise ValueError(ConfigurationErrorMessages.QDRANT_LOCAL_PATH_REQUIRED)
        if self.qdrant_mode is QdrantRuntimeMode.REMOTE and self.qdrant_url is None:
            raise ValueError(ConfigurationErrorMessages.QDRANT_REMOTE_URL_REQUIRED)
        if (
            self.environment is RuntimeEnvironment.PRODUCTION
            and self.qdrant_mode is not QdrantRuntimeMode.REMOTE
        ):
            raise ValueError(ConfigurationErrorMessages.PRODUCTION_QDRANT_MUST_BE_REMOTE)
        return self
