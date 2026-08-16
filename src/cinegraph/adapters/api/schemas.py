from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cinegraph.config import (
    DEFAULT_API_CONFIGURATION,
    DEFAULT_RECOMMENDATION_CONFIGURATION,
)
from cinegraph.domain.enums.enum import PrincipalKind, SpoilerMode, WatchPreference


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageResponse(ApiSchema):
    message: str


class HealthResponse(ApiSchema):
    status: str


class RegisterRequest(ApiSchema):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("email", "display_name")
    @classmethod
    def require_trimmed(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("Value must be trimmed.")
        return value


class LoginRequest(ApiSchema):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class SessionResponse(ApiSchema):
    principal_kind: PrincipalKind
    profile_id: UUID
    user_id: UUID | None
    corpus_scope_revision: str
    expires_at: datetime | None = None
    display_name: str | None = None


class CatalogueEpisodeResponse(ApiSchema):
    episode_id: UUID
    episode_number: int
    episode_title: str | None


class CatalogueSeasonResponse(ApiSchema):
    season_id: UUID
    season_number: int
    episodes: tuple[CatalogueEpisodeResponse, ...]


class CatalogueSeriesResponse(ApiSchema):
    series_id: UUID
    series_name: str
    seasons: tuple[CatalogueSeasonResponse, ...]


class CatalogueResponse(ApiSchema):
    schema_version: int
    corpus_scope_revision: str
    series: tuple[CatalogueSeriesResponse, ...]


class ChatRequest(ApiSchema):
    series_id: UUID
    question: str = Field(
        min_length=DEFAULT_API_CONFIGURATION.minimum_question_length,
        max_length=DEFAULT_API_CONFIGURATION.maximum_question_length,
    )
    spoiler_mode: SpoilerMode = SpoilerMode.RELAXED
    safe_through_episode_id: UUID | None = None
    limit: int = Field(
        default=DEFAULT_API_CONFIGURATION.default_retrieval_limit,
        ge=1,
        le=DEFAULT_API_CONFIGURATION.maximum_retrieval_limit,
    )

    @field_validator("question")
    @classmethod
    def require_trimmed_question(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("Question must be trimmed.")
        return value

    @model_validator(mode="after")
    def require_boundary_for_non_relaxed_mode(self) -> "ChatRequest":
        if self.spoiler_mode is SpoilerMode.RELAXED:
            if self.safe_through_episode_id is not None:
                raise ValueError("Relaxed mode cannot set a spoiler boundary.")
        elif self.safe_through_episode_id is None:
            raise ValueError("A spoiler boundary is required in protected modes.")
        return self


class CitationResponse(ApiSchema):
    segment_id: UUID
    source_version_id: UUID
    season_number: int
    episode_number: int
    start_ms: int
    end_ms: int
    text: str
    score: float


class ChatResponse(ApiSchema):
    answer: str | None
    citations: tuple[CitationResponse, ...]
    is_safe_refusal: bool


class RecommendationRequest(ApiSchema):
    series_id: UUID
    mood: str = Field(
        min_length=1,
        max_length=DEFAULT_RECOMMENDATION_CONFIGURATION.maximum_term_length,
    )
    characters: tuple[str, ...] = Field(
        default=(),
        max_length=DEFAULT_RECOMMENDATION_CONFIGURATION.maximum_characters,
    )
    excluded_themes: tuple[str, ...] = Field(
        default=(),
        max_length=DEFAULT_RECOMMENDATION_CONFIGURATION.maximum_excluded_themes,
    )
    maximum_runtime_seconds: int | None = Field(default=None, ge=1)
    watch_preference: WatchPreference = WatchPreference.ANY
    requested_count: int = Field(
        default=3,
        ge=1,
        le=DEFAULT_RECOMMENDATION_CONFIGURATION.maximum_requested_count,
    )
    spoiler_mode: SpoilerMode = SpoilerMode.RELAXED
    safe_through_episode_id: UUID | None = None

    @field_validator("mood")
    @classmethod
    def require_trimmed_mood(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("Mood must be trimmed.")
        return value

    @field_validator("characters", "excluded_themes")
    @classmethod
    def require_unique_trimmed_terms(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(
            not value
            or value.strip() != value
            or len(value) > DEFAULT_RECOMMENDATION_CONFIGURATION.maximum_term_length
            for value in values
        ):
            raise ValueError("Recommendation terms must be non-empty and trimmed.")
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("Recommendation terms must be unique.")
        return values

    @model_validator(mode="after")
    def require_boundary_for_non_relaxed_mode(self) -> "RecommendationRequest":
        if self.spoiler_mode is SpoilerMode.RELAXED:
            if self.safe_through_episode_id is not None:
                raise ValueError("Relaxed mode cannot set a spoiler boundary.")
        elif self.safe_through_episode_id is None:
            raise ValueError("A spoiler boundary is required in protected modes.")
        return self


class EpisodeRecommendationResponse(ApiSchema):
    episode_id: UUID
    season_number: int
    episode_number: int
    episode_title: str | None
    runtime_seconds: int | None
    score: float
    reason: str
    citations: tuple[CitationResponse, ...]


class RecommendationResponse(ApiSchema):
    recommendations: tuple[EpisodeRecommendationResponse, ...]
    visible_candidate_count: int
