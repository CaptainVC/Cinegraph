from typing import Protocol
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from cinegraph.application.models.episode_recommendation import (
    RankedRecommendationDraft,
    RecommendationCandidate,
    RecommendationRankingRequest,
)
from cinegraph.common.prompts import (
    RECOMMENDATION_HUMAN_PROMPT,
    RECOMMENDATION_SYSTEM_PROMPT,
)


class RecommendationItemSchema(BaseModel):
    episode_id: UUID
    score: float = Field(ge=0, le=1)
    reason: str
    cited_segment_ids: tuple[UUID, ...]


class RecommendationResponseSchema(BaseModel):
    items: tuple[RecommendationItemSchema, ...]


class StructuredRecommendationInvoker(Protocol):
    def invoke(self, prompt_variables: dict[str, str]) -> RecommendationResponseSchema: ...


def _render_candidate(candidate: RecommendationCandidate) -> str:
    evidence = "\n".join(
        (
            "BEGIN_UNTRUSTED_TRANSCRIPT_EVIDENCE\n"
            f"segment_id={item.segment_id} start_ms={item.start_ms} end_ms={item.end_ms}\n"
            f"{item.text}\nEND_UNTRUSTED_TRANSCRIPT_EVIDENCE"
        )
        for item in candidate.evidence
    )
    return (
        f"episode_id={candidate.episode.episode_id} "
        f"season={candidate.episode.position.season_number} "
        f"episode={candidate.episode.position.episode_number}\n"
        f"title={candidate.episode_title or ''}\n"
        f"runtime_seconds={candidate.runtime_seconds or ''}\n"
        f"synopsis={candidate.synopsis or ''}\n{evidence}"
    )


class LangChainEpisodeRecommendationRanker:
    def __init__(self, invoker: StructuredRecommendationInvoker) -> None:
        self._invoker = invoker

    @classmethod
    def from_chat_model(
        cls,
        model: BaseChatModel,
    ) -> "LangChainEpisodeRecommendationRanker":
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", RECOMMENDATION_SYSTEM_PROMPT),
                ("human", RECOMMENDATION_HUMAN_PROMPT),
            ]
        )
        return cls(prompt | model.with_structured_output(RecommendationResponseSchema))

    def rank(
        self,
        request: RecommendationRankingRequest,
    ) -> tuple[RankedRecommendationDraft, ...]:
        response = self._invoker.invoke(
            {
                "mood": request.mood,
                "characters": ", ".join(request.characters) or "any",
                "excluded_themes": ", ".join(request.excluded_themes) or "none",
                "requested_count": str(request.requested_count),
                "candidates": "\n\n".join(
                    _render_candidate(item) for item in request.candidates
                ),
            }
        )
        return tuple(
            RankedRecommendationDraft(
                episode_id=item.episode_id,
                score=item.score,
                reason=item.reason,
                cited_segment_ids=item.cited_segment_ids,
            )
            for item in response.items
        )
