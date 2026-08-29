from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.config import (
    DEFAULT_API_CONFIGURATION,
    DEFAULT_AUTHENTICATION_CONFIGURATION,
    DEFAULT_RECOMMENDATION_CONFIGURATION,
)
from cinegraph.domain.enums.enum import PrincipalKind, SpoilerMode, WatchPreference


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageResponse(ApiSchema):
    message: str


class HealthResponse(ApiSchema):
    status: str


class ClientConfigurationResponse(ApiSchema):
    """Validated, non-sensitive runtime values required by the product UI."""

    api_prefix: str
    agent_poll_interval_ms: StrictInt
    agent_job_deadline_ms: StrictInt

    @field_validator("agent_poll_interval_ms", "agent_job_deadline_ms")
    @classmethod
    def require_positive_integer(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(AgentJobErrorMessages.CLIENT_RUNTIME_VALUES)
        return value


class RegisterRequest(ApiSchema):
    email: str = Field(
        min_length=DEFAULT_AUTHENTICATION_CONFIGURATION.minimum_email_length,
        max_length=DEFAULT_AUTHENTICATION_CONFIGURATION.maximum_email_length,
    )
    password: str = Field(
        min_length=DEFAULT_AUTHENTICATION_CONFIGURATION.minimum_password_length,
        max_length=DEFAULT_AUTHENTICATION_CONFIGURATION.maximum_password_length,
    )
    display_name: str = Field(
        min_length=DEFAULT_AUTHENTICATION_CONFIGURATION.minimum_display_name_length,
        max_length=DEFAULT_AUTHENTICATION_CONFIGURATION.maximum_display_name_length,
    )

    @field_validator("email", "display_name")
    @classmethod
    def require_trimmed(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("Value must be trimmed.")
        return value


class LoginRequest(ApiSchema):
    email: str = Field(
        min_length=DEFAULT_AUTHENTICATION_CONFIGURATION.minimum_email_length,
        max_length=DEFAULT_AUTHENTICATION_CONFIGURATION.maximum_email_length,
    )
    password: str = Field(
        min_length=1,
        max_length=DEFAULT_AUTHENTICATION_CONFIGURATION.maximum_password_length,
    )


class SessionResponse(ApiSchema):
    principal_kind: PrincipalKind
    profile_id: UUID
    user_id: UUID | None
    corpus_scope_revision: str
    expires_at: datetime | None = None
    display_name: str | None = None


class AccountResponse(ApiSchema):
    user_id: UUID
    profile_id: UUID
    email: str
    display_name: str
    status: str
    created_at: datetime


class ProfileUpdateRequest(ApiSchema):
    display_name: str = Field(
        min_length=DEFAULT_AUTHENTICATION_CONFIGURATION.minimum_display_name_length,
        max_length=DEFAULT_AUTHENTICATION_CONFIGURATION.maximum_display_name_length,
    )

    @field_validator("display_name")
    @classmethod
    def require_trimmed_display_name(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("Display name must be trimmed.")
        return value


class PasswordChangeRequest(ApiSchema):
    current_password: str = Field(
        min_length=1,
        max_length=DEFAULT_AUTHENTICATION_CONFIGURATION.maximum_password_length,
    )
    new_password: str = Field(
        min_length=DEFAULT_AUTHENTICATION_CONFIGURATION.minimum_password_length,
        max_length=DEFAULT_AUTHENTICATION_CONFIGURATION.maximum_password_length,
    )


class SessionSummaryResponse(ApiSchema):
    session_id: UUID
    created_at: datetime
    expires_at: datetime
    current: bool


class SessionListResponse(ApiSchema):
    sessions: tuple[SessionSummaryResponse, ...]


class MetadataSourceResponse(ApiSchema):
    provider_name: str
    canonical_url: str
    attribution: str
    license_name: str
    license_url: str


class CatalogueCreditResponse(ApiSchema):
    name: str
    character_name: str
    credit_kind: str
    canonical_url: str
    character_canonical_url: str | None = None


class CataloguePosterResponse(ApiSchema):
    url: str
    alt: str
    width: int | None = None
    height: int | None = None
    attribution: str
    license_name: str
    license_url: str


class CatalogueEpisodeResponse(ApiSchema):
    episode_id: UUID
    episode_number: int
    episode_title: str | None
    guest_cast: tuple[CatalogueCreditResponse, ...] = ()


class CatalogueSeasonResponse(ApiSchema):
    season_id: UUID
    season_number: int
    episodes: tuple[CatalogueEpisodeResponse, ...]


class CatalogueSeriesResponse(ApiSchema):
    series_id: UUID
    series_name: str
    seasons: tuple[CatalogueSeasonResponse, ...]
    poster: CataloguePosterResponse | None = None
    regular_cast: tuple[CatalogueCreditResponse, ...] = ()
    metadata_source: MetadataSourceResponse | None = None


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
