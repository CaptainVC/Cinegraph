from typing import Protocol
from uuid import UUID

from cinegraph.application.models.agent_context import AgentRuntimeContext


class ConversationalAgent(Protocol):

    # Invoke the conversation agent with invocation-only context and thread identity.
    def invoke(
        self,
        question: str,
        context: AgentRuntimeContext,
        thread_id: UUID,
    ) -> dict[str, object]: ...