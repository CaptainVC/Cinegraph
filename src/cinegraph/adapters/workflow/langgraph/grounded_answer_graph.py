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
    # Initializes the object with its required state.
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

    # Executes the operation and returns its result.
    def execute(self, query: GroundedAnswerQuery) -> GroundedAnswerResult:
        # Start the graph with an empty result and no regeneration attempts.
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

    # Builds and returns the requested structure.
    def _build_graph(self) -> StateGraph:
        # Register workflow nodes and connect retrieval, drafting, verification, and refusal paths.
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

    # Processes the supplied retrieve values.
    def _retrieve(
        self, state: GroundedAnswerGraphState
    ) -> dict[str, tuple[TranscriptSegment, ...]]:
        return {
            "visible_segments": self._service.retrieve_visible_segments(
                state["query"]
            )
        }

    # Processes the supplied route after retrieve values.
    def _route_after_retrieve(self, state: GroundedAnswerGraphState) -> str:
        return "draft" if state["visible_segments"] else "safe_refusal"

    # Processes the supplied draft values.
    def _draft(self, state: GroundedAnswerGraphState) -> dict[str, ModelDraft]:
        return {
            "draft": self._service.draft_answer(
                state["query"].question, state["visible_segments"]
            )
        }

    # Processes the supplied verify values.
    def _verify(self, state: GroundedAnswerGraphState) -> dict[str, object]:
        draft = state["draft"]
        assert draft is not None
        # Convert citation validation failures into graph state for routing.
        try:
            result = self._service.validate_draft(state["visible_segments"], draft)
        except ValueError:
            return {"validation_failed": True}
        return {"result": result, "validation_failed": False}

    # Processes the supplied route after verify values.
    def _route_after_verify(self, state: GroundedAnswerGraphState) -> str:
        # End valid drafts, retry failed drafts while allowed, and then refuse safely.
        if not state["validation_failed"]:
            return "end"
        if state["retry_count"] < self._max_regeneration_attempts:
            return "regenerate"
        return "safe_refusal"

    # Processes the supplied regenerate values.
    def _regenerate(self, state: GroundedAnswerGraphState) -> dict[str, int]:
        return {"retry_count": state["retry_count"] + 1}

    # Processes the supplied safe refusal values.
    def _safe_refusal(
        self, state: GroundedAnswerGraphState
    ) -> dict[str, GroundedAnswerResult]:
        return {
            "result": GroundedAnswerResult(answer=None, citations=(), is_safe_refusal=True)
        }
