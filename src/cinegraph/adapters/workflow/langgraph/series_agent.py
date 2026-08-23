import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from cinegraph.adapters.workflow.langgraph.hybrid_grounded_answer_graph import (
    HybridGroundedAnswerGraphWorkflow,
)
from cinegraph.adapters.workflow.langgraph.series_agent_middleware import (
    build_series_agent_middleware,
)
from cinegraph.adapters.workflow.langgraph.series_graph_rag_tool import build_series_graph_rag_tool
from cinegraph.adapters.workflow.langgraph.series_transcript_answer_tool import (
    build_series_transcript_answer_tool,
)
from cinegraph.application.models.series_agent_context import SeriesAgentRuntimeContext
from cinegraph.application.models.series_agent_result import SeriesAgentCitation, SeriesAgentResult
from cinegraph.application.service.graph_rag_service import GraphRagQueryService
from cinegraph.common.error_messages import SeriesAgentErrorMessages
from cinegraph.common.prompts import SERIES_AGENT_SYSTEM_PROMPT
from cinegraph.config.series_agent import (
    DEFAULT_SERIES_AGENT_CONFIGURATION,
    SERIES_GRAPH_TOOL_NAME,
    SERIES_STRUCTURED_RESPONSE_TOOL_MESSAGE,
    SERIES_STRUCTURED_RESPONSE_TOOL_NAME,
    SERIES_TRANSCRIPT_TOOL_NAME,
    SeriesAgentConfiguration,
)


class _StructuredSeriesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: StrictStr | None = None
    citation_ids: list[UUID] = Field(default_factory=list)

    @field_validator("answer")
    @classmethod
    def _trim_answer(cls, value: str | None) -> str | None:
        if value is not None and value.strip() != value:
            raise ValueError(SeriesAgentErrorMessages.RESPONSE_ANSWER_TRIMMED)
        return value


class SeriesResearchAgent:
    """LangGraph adapter whose public result is an application DTO."""

    def __init__(
        self,
        model: BaseChatModel,
        transcript_workflow: HybridGroundedAnswerGraphWorkflow,
        graph_rag_service: GraphRagQueryService,
        checkpointer: BaseCheckpointSaver | None = None,
        tool_selector_model: BaseChatModel | None = None,
        middleware: Sequence[AgentMiddleware] | None = None,
        configuration: SeriesAgentConfiguration = DEFAULT_SERIES_AGENT_CONFIGURATION,
    ) -> None:
        self._configuration = configuration
        stack = (
            build_series_agent_middleware(tool_selector_model, configuration)
            if middleware is None
            else tuple(middleware)
        )
        self._agent: Any = create_agent(  # type: ignore[misc]
            model=model,
            tools=[
                build_series_transcript_answer_tool(transcript_workflow, configuration),
                build_series_graph_rag_tool(graph_rag_service, configuration),
            ],
            context_schema=SeriesAgentRuntimeContext,
            system_prompt=SERIES_AGENT_SYSTEM_PROMPT,
            checkpointer=checkpointer,
            middleware=stack,
            response_format=ToolStrategy(
                _StructuredSeriesResponse,
                tool_message_content=SERIES_STRUCTURED_RESPONSE_TOOL_MESSAGE,
                handle_errors=True,
            ),
        )

    def invoke(
        self, question: str, context: SeriesAgentRuntimeContext, thread_id: UUID | None = None
    ) -> SeriesAgentResult:
        if (
            not isinstance(question, str)
            or not question.strip()
            or question.strip() != question
            or len(question) > self._configuration.question_max_length
        ):
            raise ValueError(SeriesAgentErrorMessages.QUESTION_INVALID)
        invocation: dict[str, object] = {"messages": [{"role": "user", "content": question}]}
        if thread_id is None:
            state = self._agent.invoke(invocation, context=context)
        else:
            state = self._agent.invoke(
                invocation, context=context, config={"configurable": {"thread_id": str(thread_id)}}
            )
        return self._project(state, context, question)

    def _project(
        self,
        state: object,
        context: SeriesAgentRuntimeContext,
        question: str,
    ) -> SeriesAgentResult:
        if not isinstance(state, dict):
            return SeriesAgentResult(answer=None, is_safe_refusal=True)
        messages = state.get("messages", ())
        if not isinstance(messages, (tuple, list)):
            return SeriesAgentResult(answer=None, is_safe_refusal=True)
        boundary = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], HumanMessage) and messages[index].content == question
            ),
            len(messages),
        )
        citations: list[SeriesAgentCitation] = []
        used_tools: list[str] = []
        known_ids: set[UUID] = set()
        has_current_structured_response = False
        for message in messages[boundary + 1 :]:
            if not isinstance(message, ToolMessage):
                continue
            payload = self._payload(message.content)
            if (
                getattr(message, "name", None) == SERIES_STRUCTURED_RESPONSE_TOOL_NAME
                and message.content == SERIES_STRUCTURED_RESPONSE_TOOL_MESSAGE
            ):
                has_current_structured_response = True
                continue
            if not isinstance(payload, dict):
                continue
            name = str(getattr(message, "name", ""))
            if (
                name in {SERIES_TRANSCRIPT_TOOL_NAME, SERIES_GRAPH_TOOL_NAME}
                and name not in used_tools
            ):
                used_tools.append(name)
            self._collect_transcript(payload, context, citations, known_ids)
            self._collect_graph(payload, context, citations, known_ids)
        if not has_current_structured_response:
            return SeriesAgentResult(
                answer=None, is_safe_refusal=True, used_tools=tuple(used_tools)
            )
        try:
            response = _StructuredSeriesResponse.model_validate(state.get("structured_response"))
        except (ValidationError, TypeError, ValueError):
            return SeriesAgentResult(
                answer=None, is_safe_refusal=True, used_tools=tuple(used_tools)
            )
        selected = response.citation_ids
        if response.answer is None:
            if selected:
                return SeriesAgentResult(
                    answer=None, is_safe_refusal=True, used_tools=tuple(used_tools)
                )
            return SeriesAgentResult(
                answer=None, is_safe_refusal=True, used_tools=tuple(used_tools)
            )
        if (
            not response.answer
            or len(selected) == 0
            or len(selected) > self._configuration.structured_response_citation_limit
        ):
            return SeriesAgentResult(
                answer=None, is_safe_refusal=True, used_tools=tuple(used_tools)
            )
        if len(selected) != len(set(selected)) or any(item not in known_ids for item in selected):
            return SeriesAgentResult(
                answer=None, is_safe_refusal=True, used_tools=tuple(used_tools)
            )
        by_id = {citation.segment_id or citation.evidence_id: citation for citation in citations}
        projected = tuple(by_id[item] for item in selected)
        return SeriesAgentResult(
            answer=response.answer,
            is_safe_refusal=False,
            citations=projected,
            used_tools=tuple(used_tools),
        )

    @staticmethod
    def _payload(content: object) -> object:
        if isinstance(content, dict):
            return content
        if not isinstance(content, str):
            return None
        try:
            return json.loads(content)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _collect_transcript(
        payload: dict[str, object],
        context: SeriesAgentRuntimeContext,
        citations: list[SeriesAgentCitation],
        known_ids: set[UUID],
    ) -> None:
        raw = payload.get("citations")
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict) or "segment_id" not in item:
                continue
            try:
                segment_id = UUID(str(item["segment_id"]))
                episode_id = UUID(str(item["episode_id"]))
                episode = next(
                    item for item in context.candidate_episodes if item.episode_id == episode_id
                )
                start_ms = item["start_ms"]
                end_ms = item["end_ms"]
                if (
                    isinstance(start_ms, bool)
                    or not isinstance(start_ms, int)
                    or isinstance(end_ms, bool)
                    or not isinstance(end_ms, int)
                    or any(
                        isinstance(item[key], bool) or not isinstance(item[key], int)
                        for key in ("season_number", "episode_number")
                    )
                    or start_ms < 0
                    or end_ms <= start_ms
                    or item["season_number"] != episode.position.season_number
                    or item["episode_number"] != episode.position.episode_number
                ):
                    raise ValueError(SeriesAgentErrorMessages.CITATION_TRANSCRIPT_INVALID)
                citation = SeriesAgentCitation(
                    "transcript",
                    episode,
                    int(item["start_ms"]),
                    int(item["end_ms"]),
                    segment_id=segment_id,
                )
            except (ValueError, TypeError, KeyError, StopIteration):
                continue
            if segment_id not in known_ids:
                known_ids.add(segment_id)
                citations.append(citation)

    @staticmethod
    def _collect_graph(
        payload: dict[str, object],
        context: SeriesAgentRuntimeContext,
        citations: list[SeriesAgentCitation],
        known_ids: set[UUID],
    ) -> None:
        claims = payload.get("claims")
        if not isinstance(claims, list):
            return
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            try:
                claim_id = UUID(str(claim["claim_id"]))
                evidence = claim.get("evidence", [])
                if not isinstance(evidence, list):
                    continue
                for item in evidence:
                    citation = SeriesResearchAgent._project_graph_evidence(item, claim_id, context)
                    if citation is not None and citation.evidence_id not in known_ids:
                        known_ids.add(citation.evidence_id)
                        citations.append(citation)
            except (ValueError, TypeError, KeyError, StopIteration):
                continue

    @staticmethod
    def _project_graph_evidence(
        item: object, claim_id: UUID, context: SeriesAgentRuntimeContext
    ) -> SeriesAgentCitation | None:
        if not isinstance(item, dict):
            return None
        try:
            evidence_id = UUID(str(item["evidence_id"]))
            episode = next(
                episode
                for episode in context.candidate_episodes
                if episode.episode_id == UUID(str(item["episode_id"]))
            )
            start_ms = item["start_ms"]
            end_ms = item["end_ms"]
            if (
                isinstance(start_ms, bool)
                or not isinstance(start_ms, int)
                or isinstance(end_ms, bool)
                or not isinstance(end_ms, int)
                or any(
                    isinstance(item[key], bool) or not isinstance(item[key], int)
                    for key in ("season_number", "episode_number")
                )
                or start_ms < 0
                or end_ms <= start_ms
                or item["season_number"] != episode.position.season_number
                or item["episode_number"] != episode.position.episode_number
            ):
                raise ValueError(SeriesAgentErrorMessages.CITATION_GRAPH_INVALID)
            return SeriesAgentCitation(
                "graph", episode, start_ms, end_ms, claim_id=claim_id, evidence_id=evidence_id
            )
        except (ValueError, TypeError, KeyError, StopIteration):
            return None


SeriesAgent = SeriesResearchAgent
