from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from cinegraph.adapters.api.schemas import ApiSchema
from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.config import DEFAULT_AGENT_JOB_CONFIGURATION
from cinegraph.domain.enums.enum import SpoilerMode


class AgentJobRequest(ApiSchema):
    thread_id: UUID
    series_id: UUID
    question: str = Field(
        min_length=DEFAULT_AGENT_JOB_CONFIGURATION.question_min_length,
        max_length=DEFAULT_AGENT_JOB_CONFIGURATION.question_max_length,
    )
    spoiler_mode: SpoilerMode = SpoilerMode.RELAXED
    safe_through_episode_id: UUID | None = None

    @field_validator("question")
    @classmethod
    def trimmed(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError(AgentJobErrorMessages.QUESTION_TRIMMED)
        return value

    @model_validator(mode="after")
    def require_boundary_for_non_relaxed_mode(self) -> "AgentJobRequest":
        if self.spoiler_mode is SpoilerMode.RELAXED and self.safe_through_episode_id is not None:
            raise ValueError(AgentJobErrorMessages.SPOILER_BOUNDARY_RELAXED)
        if self.spoiler_mode is not SpoilerMode.RELAXED and self.safe_through_episode_id is None:
            raise ValueError(AgentJobErrorMessages.SPOILER_BOUNDARY_REQUIRED)
        return self


class AgentJobEntityResponse(ApiSchema):
    entity_id: UUID
    kind: str
    display_name: str


class AgentJobRelationshipResponse(ApiSchema):
    subject: AgentJobEntityResponse
    predicate: str
    object: AgentJobEntityResponse
    polarity: str
    hop_distance: int
    score: float


class AgentJobCitationResponse(ApiSchema):
    citation_id: UUID
    kind: str
    episode_id: UUID
    season_number: int
    episode_number: int
    start_ms: int
    end_ms: int
    segment_id: UUID | None = None
    claim_id: UUID | None = None
    evidence_id: UUID | None = None
    graph: AgentJobRelationshipResponse | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "AgentJobCitationResponse":
        if self.kind == "transcript" and (
            self.segment_id is None or self.claim_id is not None or self.evidence_id is not None
        ):
            raise ValueError(AgentJobErrorMessages.CITATION_TRANSCRIPT_SHAPE)
        if self.kind == "graph" and (
            self.segment_id is not None or self.claim_id is None or self.evidence_id is None
        ):
            raise ValueError(AgentJobErrorMessages.CITATION_GRAPH_SHAPE)
        if self.kind not in {"transcript", "graph"}:
            raise ValueError(AgentJobErrorMessages.CITATION_KIND)
        return self


class AgentJobResultResponse(ApiSchema):
    answer: str | None
    is_safe_refusal: bool
    used_tools: tuple[str, ...]
    citations: tuple[AgentJobCitationResponse, ...]
    evidence_url: str | None = None

    @model_validator(mode="after")
    def validate_coherence(self) -> "AgentJobResultResponse":
        if self.is_safe_refusal and (self.answer is not None or self.citations or self.used_tools):
            raise ValueError(AgentJobErrorMessages.REFUSAL_RESULT_SHAPE)
        if not self.is_safe_refusal and (not self.answer or not self.citations):
            raise ValueError(AgentJobErrorMessages.GROUNDED_RESULT_SHAPE)
        return self


class AgentJobResponse(ApiSchema):
    job_id: UUID
    thread_id: UUID
    series_id: UUID
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: AgentJobResultResponse | None = None
    error_code: str | None = None
    status_url: str
    events_url: str


class AgentEvidenceExcerptResponse(ApiSchema):
    citation_id: UUID
    excerpt: str


class AgentEvidenceResponse(ApiSchema):
    job_id: UUID
    items: tuple[AgentEvidenceExcerptResponse, ...]
