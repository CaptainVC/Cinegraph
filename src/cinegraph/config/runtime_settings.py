from enum import StrEnum
from pathlib import Path

from pydantic import AnyHttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from cinegraph.common.error_messages import ConfigurationErrorMessages
from cinegraph.config.database import DEFAULT_DATABASE_CONFIGURATION


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
    identity_database_path: Path = DEFAULT_DATABASE_CONFIGURATION.development_path
    database_url: SecretStr | None = None
    database_pool_size: int = DEFAULT_DATABASE_CONFIGURATION.pool_size
    database_max_overflow: int = DEFAULT_DATABASE_CONFIGURATION.max_overflow
    database_pool_timeout_seconds: int = (
        DEFAULT_DATABASE_CONFIGURATION.pool_timeout_seconds
    )
    database_pool_recycle_seconds: int = (
        DEFAULT_DATABASE_CONFIGURATION.pool_recycle_seconds
    )
    qdrant_mode: QdrantRuntimeMode = QdrantRuntimeMode.LOCAL
    qdrant_local_path: Path | None = Path("knowledge/qdrant-development")
    qdrant_url: AnyHttpUrl | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_collection_name: str = "transcript_segments_development"
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    @field_validator("qdrant_collection_name")
    @classmethod
    def require_trimmed_collection_name(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError(
                ConfigurationErrorMessages.QDRANT_COLLECTION_NAME_MUST_BE_TRIMMED
            )
        return value

    @field_validator("api_host")
    @classmethod
    def require_trimmed_api_host(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("API host must be non-empty and trimmed.")
        return value

    @field_validator("api_port")
    @classmethod
    def require_valid_api_port(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1 or value > 65_535:
            raise ValueError("API port must be between 1 and 65535.")
        return value

    @field_validator(
        "database_pool_size",
        "database_max_overflow",
        "database_pool_timeout_seconds",
        "database_pool_recycle_seconds",
    )
    @classmethod
    def require_positive_database_pool_setting(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError(
                ConfigurationErrorMessages.DATABASE_POOL_SETTINGS_MUST_BE_POSITIVE
            )
        return value

    @model_validator(mode="after")
    def require_environment_compatible_services(self) -> "CinegraphRuntimeSettings":
        if self.qdrant_mode is QdrantRuntimeMode.LOCAL and self.qdrant_local_path is None:
            raise ValueError(ConfigurationErrorMessages.QDRANT_LOCAL_PATH_REQUIRED)
        if self.qdrant_mode is QdrantRuntimeMode.REMOTE and self.qdrant_url is None:
            raise ValueError(ConfigurationErrorMessages.QDRANT_REMOTE_URL_REQUIRED)
        if (
            self.environment is RuntimeEnvironment.PRODUCTION
            and self.qdrant_mode is not QdrantRuntimeMode.REMOTE
        ):
            raise ValueError(ConfigurationErrorMessages.PRODUCTION_QDRANT_MUST_BE_REMOTE)
        if self.database_url is None:
            self.database_url = SecretStr(
                DEFAULT_DATABASE_CONFIGURATION.sqlite_url(
                    self.identity_database_path
                )
            )
        database_url = self.database_url.get_secret_value()
        if not database_url or database_url.strip() != database_url:
            raise ValueError(ConfigurationErrorMessages.DATABASE_URL_MUST_BE_TRIMMED)
        try:
            parsed_database_url = make_url(database_url)
        except ArgumentError as error:
            raise ValueError(ConfigurationErrorMessages.DATABASE_URL_MUST_BE_VALID) from error
        if (
            parsed_database_url.drivername
            not in DEFAULT_DATABASE_CONFIGURATION.supported_driver_names
        ):
            raise ValueError(
                ConfigurationErrorMessages.DATABASE_DIALECT_MUST_BE_SUPPORTED
            )
        if (
            self.environment is RuntimeEnvironment.PRODUCTION
            and parsed_database_url.drivername
            != DEFAULT_DATABASE_CONFIGURATION.postgresql_driver_name
        ):
            raise ValueError(ConfigurationErrorMessages.PRODUCTION_DATABASE_MUST_BE_POSTGRES)
        return self
