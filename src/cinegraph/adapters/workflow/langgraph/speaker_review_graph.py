from pathlib import Path
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from cinegraph.common.error_messages import WorkflowErrorMessages
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
    load_run_state,
)

SpeakerReviewGraphOperation = Literal[
    "start",
    "submit",
    "advance",
    "final-review",
    "retry-incomplete",
    "reconcile-costs",
]


class SpeakerReviewGraphState(TypedDict):
    operation: SpeakerReviewGraphOperation
    corpus_root: Path | None
    seasons: tuple[int, ...]
    run_directory: Path | None
    run_state: SpeakerReviewRunState | None


class SpeakerReviewGraphWorkflow:
    """Use LangGraph to orchestrate resumable deterministic corpus review stages."""

    def __init__(self, workflow: SpeakerReviewWorkflow) -> None:
        self._workflow = workflow
        self._graph = self._build_graph().compile()

    def start(
        self,
        *,
        corpus_root: Path,
        seasons: tuple[int, ...],
    ) -> tuple[Path, SpeakerReviewRunState]:
        return self._invoke(
            operation="start",
            corpus_root=corpus_root,
            seasons=seasons,
            run_directory=None,
        )

    def submit(
        self,
        run_directory: Path,
    ) -> tuple[Path, SpeakerReviewRunState]:
        return self._invoke(
            operation="submit",
            corpus_root=None,
            seasons=(),
            run_directory=run_directory,
        )

    def advance(
        self,
        run_directory: Path,
    ) -> tuple[Path, SpeakerReviewRunState]:
        return self._invoke(
            operation="advance",
            corpus_root=None,
            seasons=(),
            run_directory=run_directory,
        )

    def final_review(
        self,
        run_directory: Path,
    ) -> tuple[Path, SpeakerReviewRunState]:
        return self._invoke(
            operation="final-review",
            corpus_root=None,
            seasons=(),
            run_directory=run_directory,
        )

    def retry_incomplete(
        self,
        run_directory: Path,
    ) -> tuple[Path, SpeakerReviewRunState]:
        return self._invoke(
            operation="retry-incomplete",
            corpus_root=None,
            seasons=(),
            run_directory=run_directory,
        )

    def reconcile_costs(
        self,
        run_directory: Path,
    ) -> tuple[Path, SpeakerReviewRunState]:
        return self._invoke(
            operation="reconcile-costs",
            corpus_root=None,
            seasons=(),
            run_directory=run_directory,
        )

    def _invoke(
        self,
        *,
        operation: SpeakerReviewGraphOperation,
        corpus_root: Path | None,
        seasons: tuple[int, ...],
        run_directory: Path | None,
    ) -> tuple[Path, SpeakerReviewRunState]:
        final_state = self._graph.invoke(
            SpeakerReviewGraphState(
                operation=operation,
                corpus_root=corpus_root,
                seasons=seasons,
                run_directory=run_directory,
                run_state=None,
            )
        )
        final_directory = final_state["run_directory"]
        final_run_state = final_state["run_state"]
        if final_directory is None or final_run_state is None:
            raise RuntimeError(
                WorkflowErrorMessages.SPEAKER_REVIEW_GRAPH_RESULT_REQUIRED
            )
        return final_directory, final_run_state

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(SpeakerReviewGraphState)
        graph.add_node("prepare", self._prepare)
        graph.add_node("load", self._load)
        graph.add_node("submit", self._submit)
        graph.add_node("advance", self._advance)
        graph.add_node("final_review", self._final_review)
        graph.add_node("retry_incomplete", self._retry_incomplete)
        graph.add_node("reconcile_costs", self._reconcile_costs)

        graph.add_conditional_edges(
            START,
            self._route_from_start,
            {"prepare": "prepare", "load": "load"},
        )
        graph.add_conditional_edges(
            "prepare",
            self._route_after_state,
            {
                "submit": "submit",
                "advance": "advance",
                "final_review": "final_review",
                "retry_incomplete": "retry_incomplete",
                "reconcile_costs": "reconcile_costs",
                "end": END,
            },
        )
        graph.add_conditional_edges(
            "load",
            self._route_after_state,
            {
                "submit": "submit",
                "advance": "advance",
                "final_review": "final_review",
                "retry_incomplete": "retry_incomplete",
                "reconcile_costs": "reconcile_costs",
                "end": END,
            },
        )
        graph.add_edge("submit", END)
        graph.add_edge("advance", END)
        graph.add_edge("final_review", END)
        graph.add_edge("retry_incomplete", END)
        graph.add_edge("reconcile_costs", END)
        return graph

    def _route_from_start(self, state: SpeakerReviewGraphState) -> str:
        return "prepare" if state["operation"] == "start" else "load"

    def _prepare(
        self,
        state: SpeakerReviewGraphState,
    ) -> dict[str, object]:
        corpus_root = state["corpus_root"]
        if corpus_root is None:
            raise RuntimeError(
                WorkflowErrorMessages.SPEAKER_REVIEW_CORPUS_ROOT_REQUIRED
            )
        run_directory, run_state = self._workflow.prepare(
            corpus_root=corpus_root,
            seasons=state["seasons"],
        )
        return {"run_directory": run_directory, "run_state": run_state}

    def _load(
        self,
        state: SpeakerReviewGraphState,
    ) -> dict[str, SpeakerReviewRunState]:
        run_directory = state["run_directory"]
        if run_directory is None:
            raise RuntimeError(
                WorkflowErrorMessages.SPEAKER_REVIEW_RUN_DIRECTORY_REQUIRED
            )
        return {"run_state": load_run_state(run_directory / "run-state.json")}

    def _route_after_state(self, state: SpeakerReviewGraphState) -> str:
        run_state = state["run_state"]
        if run_state is None:
            return "end"
        if (
            state["operation"] in {"start", "submit"}
            and run_state.status is SpeakerReviewRunStatus.PREPARED
        ):
            return "submit"
        if (
            state["operation"] == "advance"
            and run_state.status
            in {
                SpeakerReviewRunStatus.PRIMARY_SUBMITTED,
                SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
                SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
            }
        ):
            return "advance"
        if (
            state["operation"] == "final-review"
            and run_state.status is SpeakerReviewRunStatus.NEEDS_HUMAN
        ):
            return "final_review"
        if (
            state["operation"] == "retry-incomplete"
            and run_state.status is SpeakerReviewRunStatus.NEEDS_HUMAN
        ):
            return "retry_incomplete"
        if state["operation"] == "reconcile-costs":
            return "reconcile_costs"
        return "end"

    def _submit(
        self,
        state: SpeakerReviewGraphState,
    ) -> dict[str, SpeakerReviewRunState]:
        run_directory, run_state = self._required_run_context(state)
        return {
            "run_state": self._workflow.submit_primary(
                run_directory,
                run_state,
            )
        }

    def _advance(
        self,
        state: SpeakerReviewGraphState,
    ) -> dict[str, SpeakerReviewRunState]:
        run_directory, run_state = self._required_run_context(state)
        return {
            "run_state": self._workflow.advance(
                run_directory,
                run_state,
            )
        }

    def _final_review(
        self,
        state: SpeakerReviewGraphState,
    ) -> dict[str, SpeakerReviewRunState]:
        run_directory, run_state = self._required_run_context(state)
        return {
            "run_state": self._workflow.submit_final_review(
                run_directory,
                run_state,
            )
        }

    def _retry_incomplete(
        self,
        state: SpeakerReviewGraphState,
    ) -> dict[str, SpeakerReviewRunState]:
        run_directory, run_state = self._required_run_context(state)
        return {
            "run_state": self._workflow.retry_incomplete_final_review(
                run_directory,
                run_state,
            )
        }

    def _reconcile_costs(
        self,
        state: SpeakerReviewGraphState,
    ) -> dict[str, SpeakerReviewRunState]:
        run_directory, run_state = self._required_run_context(state)
        return {
            "run_state": self._workflow.reconcile_completed_costs(
                run_directory,
                run_state,
            )
        }

    def _required_run_context(
        self,
        state: SpeakerReviewGraphState,
    ) -> tuple[Path, SpeakerReviewRunState]:
        run_directory = state["run_directory"]
        run_state = state["run_state"]
        if run_directory is None or run_state is None:
            raise RuntimeError(
                WorkflowErrorMessages.SPEAKER_REVIEW_GRAPH_RESULT_REQUIRED
            )
        return run_directory, run_state
