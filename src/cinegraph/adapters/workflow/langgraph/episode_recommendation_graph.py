from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from cinegraph.application.models.episode_recommendation import (
    RankedRecommendationDraft,
    RecommendationCandidate,
    RecommendEpisodesQuery,
    RecommendEpisodesResult,
)
from cinegraph.application.service.episode_recommendation_service import (
    EpisodeRecommendationService,
)
from cinegraph.common.error_messages import RecommendationErrorMessages


class EpisodeRecommendationGraphState(TypedDict):
    query: RecommendEpisodesQuery
    visible_candidates: tuple[RecommendationCandidate, ...]
    ranked_candidates: tuple[RecommendationCandidate, ...]
    drafts: tuple[RankedRecommendationDraft, ...]
    result: RecommendEpisodesResult | None


class EpisodeRecommendationGraphWorkflow:
    def __init__(self, service: EpisodeRecommendationService) -> None:
        self._service = service
        self._graph = self._build_graph().compile()

    def execute(self, query: RecommendEpisodesQuery) -> RecommendEpisodesResult:
        state = self._graph.invoke(
            EpisodeRecommendationGraphState(
                query=query,
                visible_candidates=(),
                ranked_candidates=(),
                drafts=(),
                result=None,
            )
        )
        result = state["result"]
        if result is None:
            raise RuntimeError(
                RecommendationErrorMessages.WORKFLOW_RESULT_MUST_EXIST
            )
        return result

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(EpisodeRecommendationGraphState)
        graph.add_node("filter", self._filter)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("rank", self._rank)
        graph.add_node("validate", self._validate)
        graph.add_node("empty", self._empty)
        graph.add_edge(START, "filter")
        graph.add_conditional_edges(
            "filter",
            lambda state: "retrieve" if state["visible_candidates"] else "empty",
            {"retrieve": "retrieve", "empty": "empty"},
        )
        graph.add_conditional_edges(
            "retrieve",
            lambda state: "rank" if state["ranked_candidates"] else "empty",
            {"rank": "rank", "empty": "empty"},
        )
        graph.add_edge("rank", "validate")
        graph.add_edge("validate", END)
        graph.add_edge("empty", END)
        return graph

    def _filter(self, state: EpisodeRecommendationGraphState) -> dict[str, object]:
        return {"visible_candidates": self._service.filter_candidates(state["query"])}

    def _retrieve(self, state: EpisodeRecommendationGraphState) -> dict[str, object]:
        return {
            "ranked_candidates": self._service.retrieve_candidate_evidence(
                state["query"], state["visible_candidates"]
            )
        }

    def _rank(self, state: EpisodeRecommendationGraphState) -> dict[str, object]:
        return {
            "drafts": self._service.rank_candidates(
                state["query"], state["ranked_candidates"]
            )
        }

    def _validate(self, state: EpisodeRecommendationGraphState) -> dict[str, object]:
        return {
            "result": self._service.validate_ranked_candidates(
                state["query"],
                state["ranked_candidates"],
                state["drafts"],
                visible_candidate_count=len(state["visible_candidates"]),
            )
        }

    def _empty(self, state: EpisodeRecommendationGraphState) -> dict[str, object]:
        return {
            "result": self._service.empty_result(
                len(state["visible_candidates"])
            )
        }
