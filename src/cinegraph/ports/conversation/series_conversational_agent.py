from typing import Protocol
from uuid import UUID

from cinegraph.application.models.series_agent_context import SeriesAgentRuntimeContext
from cinegraph.application.models.series_agent_result import SeriesAgentResult


class SeriesConversationalAgent(Protocol):
    def invoke(
        self,
        question: str,
        context: SeriesAgentRuntimeContext,
        thread_id: UUID,
    ) -> SeriesAgentResult: ...
