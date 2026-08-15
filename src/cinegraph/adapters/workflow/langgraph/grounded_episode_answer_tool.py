from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from cinegraph.adapters.workflow.langgraph.grounded_answer_graph import (
    GroundedAnswerGraphWorkflow,
)
from cinegraph.application.models.agent_context import AgentRuntimeContext
from cinegraph.application.models.grounded_answer import GroundedAnswerQuery


def build_grounded_episode_answer_tool(
    workflow: GroundedAnswerGraphWorkflow,
) -> BaseTool:
    # Create the only model-visible argument while closing over the workflow adapter.
    @tool(description="Answer an episode-specific question from validated grounded evidence.")
    # Execute grounded retrieval with invocation-only runtime context.
    def grounded_episode_answer(
        question: str,
        runtime: ToolRuntime[AgentRuntimeContext, dict[str, object]],
    ) -> dict[str, object]:
        # Assemble the domain query exclusively from the injected context.
        context = runtime.context
        query = GroundedAnswerQuery(
            question=question,
            episode=context["episode"],
            summary_source_document_id=context["summary_source_document_id"],
            profile_watch_state=context["profile_watch_state"],
        )
        result = workflow.execute(query)

        # Project only safe answer and citation metadata, never transcript text.
        citations = [
            {
                "segment_id": str(citation.segment_id),
                "season_number": citation.episode.position.season_number,
                "episode_number": citation.episode.position.episode_number,
                "start_ms": citation.start_ms,
                "end_ms": citation.end_ms,
            }
            for citation in result.citations
        ]
        return {
            "answer": result.answer,
            "is_safe_refusal": result.is_safe_refusal,
            "citations": citations,
        }

    return grounded_episode_answer
