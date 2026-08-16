from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cinegraph.config.models import DEFAULT_MODEL_CONFIGURATION


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openai_api_key: SecretStr
    main_model: str = DEFAULT_MODEL_CONFIGURATION.main_model
    rag_answer_model: str = DEFAULT_MODEL_CONFIGURATION.rag_answer_model
    recommendation_model: str = DEFAULT_MODEL_CONFIGURATION.recommendation_model
    speaker_review_model: str = DEFAULT_MODEL_CONFIGURATION.speaker_review_model
    speaker_adjudication_model: str = (
        DEFAULT_MODEL_CONFIGURATION.speaker_adjudication_model
    )
    speaker_final_review_model: str = (
        DEFAULT_MODEL_CONFIGURATION.speaker_final_review_model
    )
    speaker_review_reasoning_effort: str = (
        DEFAULT_MODEL_CONFIGURATION.speaker_review_reasoning_effort
    )
    speaker_adjudication_reasoning_effort: str = (
        DEFAULT_MODEL_CONFIGURATION.speaker_adjudication_reasoning_effort
    )
    speaker_final_review_reasoning_effort: str = (
        DEFAULT_MODEL_CONFIGURATION.speaker_final_review_reasoning_effort
    )

    @field_validator(
        "main_model",
        "rag_answer_model",
        "recommendation_model",
        "speaker_review_model",
        "speaker_adjudication_model",
        "speaker_final_review_model",
        "speaker_review_reasoning_effort",
        "speaker_adjudication_reasoning_effort",
        "speaker_final_review_reasoning_effort",
    )
    @classmethod
    def require_trimmed_non_empty_value(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("Model configuration values must be non-empty and trimmed.")
        return value
