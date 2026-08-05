from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from cinegraph.application.models.grounded_answer import (
    GroundedAnswerQuery,
    GroundedAnswerResult,
    ModelDraft,
)
from cinegraph.application.service.grounded_answer_service import (
    GroundedAnswerService,
)
from cinegraph.common.error_messages import WorkflowErrorMessages
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment

DEFAULT_MAX_REGENERATION_ATTEMPTS = 1


class GroundedAnswerGraphState(TypedDict):
    query: GroundedAnswerQuery
    visible_segments: tuple[TranscriptSegment, ...]
    draft: ModelDraft | None
    result: GroundedAnswerResult | None
    retry_count: int
    validation_failed: bool


class GroundedAnswerGraphWorkflow:
    def __init__(
        self,
        service: GroundedAnswerService,
        max_regeneration_attempts: int = DEFAULT_MAX_REGENERATION_ATTEMPTS,
    ) -> None:
        if max_regeneration_attempts < 0:
            raise ValueError(
                WorkflowErrorMessages.MAX_REGENERATION_ATTEMPTS_MUST_BE_NON_NEGATIVE
            )
        self._service = service
        self._max_regeneration_attempts = max_regeneration_attempts
        self._graph = self._build_graph().compile()

    def execute(self, query: GroundedAnswerQuery) -> GroundedAnswerResult:
        final_state = self._graph.invoke(
            GroundedAnswerGraphState(
                query=query,
                visible_segments=(),
                draft=None,
                result=None,
                retry_count=0,
                validation_failed=False,
            )
        )
        result = final_state["result"]
        if result is None:
            raise RuntimeError(
                WorkflowErrorMessages.COMPLETED_WORKFLOW_RESULT_CANNOT_BE_NONE
            )
        return result

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(GroundedAnswerGraphState)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("draft", self._draft)
        graph.add_node("verify", self._verify)
        graph.add_node("regenerate", self._regenerate)
        graph.add_node("safe_refusal", self._safe_refusal)

        graph.add_edge(START, "retrieve")
        graph.add_conditional_edges(
            "retrieve",
            self._route_after_retrieve,
            {"draft": "draft", "safe_refusal": "safe_refusal"},
        )
        graph.add_edge("draft", "verify")
        graph.add_conditional_edges(
            "verify",
            self._route_after_verify,
            {
                "end": END,
                "regenerate": "regenerate",
                "safe_refusal": "safe_refusal",
            },
        )
        graph.add_edge("regenerate", "draft")
        graph.add_edge("safe_refusal", END)
        return graph

    def _retrieve(
        self, state: GroundedAnswerGraphState
    ) -> dict[str, tuple[TranscriptSegment, ...]]:
        return {
            "visible_segments": self._service.retrieve_visible_segments(
                state["query"]
            )
        }

    def _route_after_retrieve(self, state: GroundedAnswerGraphState) -> str:
        return "draft" if state["visible_segments"] else "safe_refusal"

    def _draft(self, state: GroundedAnswerGraphState) -> dict[str, ModelDraft]:
        return {
            "draft": self._service.draft_answer(
                state["query"].question, state["visible_segments"]
            )
        }

    def _verify(self, state: GroundedAnswerGraphState) -> dict[str, object]:
        draft = state["draft"]
        assert draft is not None
        try:
            result = self._service.validate_draft(state["visible_segments"], draft)
        except ValueError:
            return {"validation_failed": True}
        return {"result": result, "validation_failed": False}

    def _route_after_verify(self, state: GroundedAnswerGraphState) -> str:
        if not state["validation_failed"]:
            return "end"
        if state["retry_count"] < self._max_regeneration_attempts:
            return "regenerate"
        return "safe_refusal"

    def _regenerate(self, state: GroundedAnswerGraphState) -> dict[str, int]:
        return {"retry_count": state["retry_count"] + 1}

    def _safe_refusal(
        self, state: GroundedAnswerGraphState
    ) -> dict[str, GroundedAnswerResult]:
        return {
            "result": GroundedAnswerResult(answer=None, citations=(), is_safe_refusal=True)
        }
