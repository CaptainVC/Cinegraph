from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from cinegraph.adapters.workflow.langgraph.grounded_answer_graph import (
    GroundedAnswerGraphWorkflow,
)
from cinegraph.adapters.workflow.langgraph.grounded_episode_answer_tool import (
    build_grounded_episode_answer_tool,
)
from cinegraph.application.models.agent_context import AgentRuntimeContext


class GroundedAnswerAgent:
    # Compile the tool-calling agent without persistence or context-injecting middleware.
    def __init__(
        self,
        model: BaseChatModel,
        workflow: GroundedAnswerGraphWorkflow,
    ) -> None:
        # Bind the deterministic workflow tool and invocation-only context schema.
        self._agent = create_agent(
            model=model,
            tools=[build_grounded_episode_answer_tool(workflow)],
            context_schema=AgentRuntimeContext,
            system_prompt=(
                "Use the grounded_episode_answer tool for episode-specific questions. "
                "Do not invent profile or episode state. The tool output is the only "
                "source of grounded answers and citations."
            ),
        )

    # Invoke the compiled agent with the question and runtime-only context.
    def invoke(
        self,
        question: str,
        context: AgentRuntimeContext,
    ) -> dict[str, object]:
        # Keep runtime context outside messages and state input.
        result = self._agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            context=context,
        )
        return dict(result)