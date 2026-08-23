from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from cinegraph.adapters.catalogue.json_catalogue_manifest_loader import (
    JsonCatalogueManifestLoader,
)
from cinegraph.adapters.llm.langchain_chat_model_gateway import (
    LangChainChatModelGateway,
)
from cinegraph.adapters.llm.langchain_recommendation_ranker import (
    LangChainEpisodeRecommendationRanker,
)
from cinegraph.adapters.persistence.sqlalchemy_graph_claim_reader import SqlAlchemyGraphClaimReader
from cinegraph.adapters.repository.in_memory.in_memory_agent_job_repository import (
    InMemoryAgentJobRepository,
)
from cinegraph.adapters.repository.in_memory.in_memory_conversation_thread_binding_repository import (
    InMemoryConversationThreadBindingRepository,
)
from cinegraph.adapters.repository.in_memory.server_owned_watch_progress_repository import (
    ServerOwnedWatchProgressRepository,
)
from cinegraph.adapters.workflow.langgraph.episode_recommendation_graph import (
    EpisodeRecommendationGraphWorkflow,
)
from cinegraph.adapters.workflow.langgraph.hybrid_grounded_answer_graph import (
    HybridGroundedAnswerGraphWorkflow,
)
from cinegraph.adapters.workflow.langgraph.series_agent import SeriesResearchAgent
from cinegraph.application.models.episode_recommendation import (
    RecommendEpisodesQuery,
    RecommendEpisodesResult,
)
from cinegraph.application.models.hybrid_grounded_answer import (
    HybridGroundedAnswerQuery,
    HybridGroundedAnswerResult,
)
from cinegraph.application.service.agent_job_service import AgentJobService, AgentJobServiceProtocol
from cinegraph.application.service.conversational_series_chat_service import (
    ConversationalSeriesChatService,
)
from cinegraph.application.service.episode_recommendation_service import (
    EpisodeRecommendationService,
)
from cinegraph.application.service.graph_rag_service import GraphRagQueryService
from cinegraph.application.service.hybrid_grounded_answer_service import (
    HybridGroundedAnswerService,
)
from cinegraph.application.service.identity_session_service import (
    IdentitySessionService,
)
from cinegraph.bootstrap.composition_root import CinegraphCompositionRoot
from cinegraph.config import (
    DEFAULT_AGENT_JOB_CONFIGURATION,
    DEFAULT_API_CONFIGURATION,
    CinegraphRuntimeSettings,
    OpenAISettings,
)
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.domain.retrieval import RetrievalScopeCompiler
from cinegraph.ports.agent_jobs.dispatcher import BoundedThreadPoolAgentJobDispatcher


class AnswerWorkflow(Protocol):
    def execute(
        self,
        query: HybridGroundedAnswerQuery,
    ) -> HybridGroundedAnswerResult: ...


class RecommendationWorkflow(Protocol):
    def execute(
        self,
        query: RecommendEpisodesQuery,
    ) -> RecommendEpisodesResult: ...


SettingsT = TypeVar("SettingsT")


def _load_settings(factory: Callable[..., SettingsT], env_file: Path) -> SettingsT:
    return factory(_env_file=env_file)


@dataclass(slots=True)
class ApiContext:
    settings: CinegraphRuntimeSettings
    catalogue: CatalogueManifest
    identity_sessions: IdentitySessionService
    answer_workflow: AnswerWorkflow
    readiness_probe: Callable[[], bool]
    recommendation_workflow: RecommendationWorkflow | None = None
    agent_job_service: AgentJobServiceProtocol | None = None
    close_callback: Callable[[], None] = lambda: None
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def agent_jobs(self) -> AgentJobServiceProtocol | None:
        return self.agent_job_service

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.agent_job_service is not None:
            self.agent_job_service.close()
        self.close_callback()


def build_default_api_context(env_file: Path = Path(".env")) -> ApiContext:
    settings = _load_settings(CinegraphRuntimeSettings, env_file)
    openai = _load_settings(OpenAISettings, env_file)
    loaded_catalogue = JsonCatalogueManifestLoader().load(
        settings.knowledge_root / DEFAULT_API_CONFIGURATION.catalogue_manifest_filename
    )
    root = CinegraphCompositionRoot(settings)
    chat_model = ChatOpenAI(
        model=openai.rag_answer_model,
        api_key=openai.openai_api_key,
        temperature=0,
    )
    answer_workflow = HybridGroundedAnswerGraphWorkflow(
        HybridGroundedAnswerService(
            root.hybrid_search_service,
            LangChainChatModelGateway.from_chat_model(chat_model),
        )
    )
    recommendation_model = (
        chat_model
        if openai.recommendation_model == openai.rag_answer_model
        else ChatOpenAI(
            model=openai.recommendation_model,
            api_key=openai.openai_api_key,
            temperature=0,
        )
    )
    recommendation_workflow = EpisodeRecommendationGraphWorkflow(
        EpisodeRecommendationService(
            loaded_catalogue.manifest,
            root.hybrid_search_service,
            LangChainEpisodeRecommendationRanker.from_chat_model(
                recommendation_model
            ),
        )
    )
    graph_rag_service = GraphRagQueryService(
        RetrievalScopeCompiler(SpoilerPolicy()),
        SqlAlchemyGraphClaimReader(root.identity_engine),
    )
    synthesis_model = ChatOpenAI(
        model=openai.agent_synthesis_model,
        api_key=openai.openai_api_key,
        temperature=0,
        reasoning_effort=openai.agent_synthesis_reasoning_effort,
        timeout=DEFAULT_AGENT_JOB_CONFIGURATION.provider_timeout_seconds,
        max_retries=0,
        store=False,
    )
    selector_model = ChatOpenAI(
        model=openai.agent_tool_selector_model,
        api_key=openai.openai_api_key,
        temperature=0,
        reasoning_effort=openai.agent_tool_selector_reasoning_effort,
        timeout=DEFAULT_AGENT_JOB_CONFIGURATION.provider_timeout_seconds,
        max_retries=0,
        store=False,
    )
    conversation_service = ConversationalSeriesChatService(
        ServerOwnedWatchProgressRepository(),
        InMemoryConversationThreadBindingRepository(),
        SeriesResearchAgent(
            synthesis_model,
            answer_workflow,
            graph_rag_service,
            checkpointer=InMemorySaver(),
            tool_selector_model=selector_model,
        ),
    )
    agent_job_service = AgentJobService(
        InMemoryAgentJobRepository(),
        conversation_service,
        BoundedThreadPoolAgentJobDispatcher(),
    )

    def readiness_probe() -> bool:
        try:
            return root.qdrant_client.collection_exists(
                root.qdrant_schema.collection_name
            )
        except Exception:
            return False

    return ApiContext(
        settings=settings,
        catalogue=loaded_catalogue.manifest,
        identity_sessions=root.identity_session_service,
        answer_workflow=answer_workflow,
        readiness_probe=readiness_probe,
        recommendation_workflow=recommendation_workflow,
        agent_job_service=agent_job_service,
        close_callback=root.close,
    )
