from uuid import UUID

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from cinegraph.adapters.workflow.langgraph.grounded_answer_graph import (
    GroundedAnswerGraphWorkflow,
)
from cinegraph.adapters.workflow.langgraph.grounded_episode_answer_tool import (
    build_grounded_episode_answer_tool,
)
from cinegraph.application.models.agent_context import AgentRuntimeContext


class GroundedAnswerAgent:
    # Compile the tool-calling agent with an optional checkpointer; invocation context is not injected into messages.
    def __init__(
        self,
        model: BaseChatModel,
        workflow: GroundedAnswerGraphWorkflow,
        checkpointer: BaseCheckpointSaver | None = None,
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
            checkpointer=checkpointer,
        )

    # Invoke the agent; a thread ID enables checkpointed message history only when a saver was injected.
    def invoke(
        self,
        question: str,
        context: AgentRuntimeContext,
        thread_id: UUID | None = None,
    ) -> dict[str, object]:
        # Keep runtime context outside messages and state input.
        invocation = {"messages": [{"role": "user", "content": question}]}
        if thread_id is None:
            result = self._agent.invoke(invocation, context=context)
        else:
            # Supply the thread ID so an injected saver can select checkpointed message history.
            result = self._agent.invoke(
                invocation,
                context=context,
                config={"configurable": {"thread_id": str(thread_id)}},
            )
        return dict(result)
