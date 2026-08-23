from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from cinegraph.adapters.workflow.langgraph.hybrid_grounded_answer_graph import (
    HybridGroundedAnswerGraphWorkflow,
)
from cinegraph.application.models.hybrid_grounded_answer import HybridGroundedAnswerQuery
from cinegraph.application.models.series_agent_context import SeriesAgentRuntimeContext
from cinegraph.common.error_messages import SeriesAgentErrorMessages
from cinegraph.config.series_agent import (
    DEFAULT_SERIES_AGENT_CONFIGURATION,
    SERIES_TRANSCRIPT_TOOL_DESCRIPTION,
    SERIES_TRANSCRIPT_TOOL_NAME,
    SeriesAgentConfiguration,
)


def build_series_transcript_answer_tool(
    workflow: HybridGroundedAnswerGraphWorkflow,
    configuration: SeriesAgentConfiguration = DEFAULT_SERIES_AGENT_CONFIGURATION,
) -> BaseTool:
    @tool(SERIES_TRANSCRIPT_TOOL_NAME, description=SERIES_TRANSCRIPT_TOOL_DESCRIPTION)
    def grounded_transcript_answer(
        question: str,
        runtime: ToolRuntime[SeriesAgentRuntimeContext, dict[str, object]],
    ) -> dict[str, object]:
        if (
            not isinstance(question, str)
            or not question.strip()
            or question.strip() != question
            or len(question) > configuration.transcript_question_max_length
        ):
            raise ValueError(SeriesAgentErrorMessages.TRANSCRIPT_ARGUMENT_INVALID)
        context = runtime.context
        query = HybridGroundedAnswerQuery(
            question=question,
            series_id=context.series_id,
            candidate_episodes=context.candidate_episodes,
            profile_watch_state=context.profile_watch_state,
            corpus_access_scope=context.corpus_access_scope,
            limit=configuration.transcript_retrieval_limit,
        )
        result = workflow.execute(query)
        citations = [
            {
                "segment_id": str(item.segment_id),
                "episode_id": str(item.episode.episode_id),
                "season_number": item.episode.position.season_number,
                "episode_number": item.episode.position.episode_number,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
            }
            for item in result.citations
        ]
        return {
            "answer": result.answer,
            "is_safe_refusal": result.is_safe_refusal,
            "citations": citations,
        }

    return grounded_transcript_answer


build_grounded_series_answer_tool = build_series_transcript_answer_tool
