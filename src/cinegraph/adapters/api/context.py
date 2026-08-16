from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from langchain_openai import ChatOpenAI

from cinegraph.adapters.catalogue.json_catalogue_manifest_loader import (
    JsonCatalogueManifestLoader,
)
from cinegraph.adapters.llm.langchain_chat_model_gateway import (
    LangChainChatModelGateway,
)
from cinegraph.adapters.workflow.langgraph.hybrid_grounded_answer_graph import (
    HybridGroundedAnswerGraphWorkflow,
)
from cinegraph.application.models.hybrid_grounded_answer import (
    HybridGroundedAnswerQuery,
    HybridGroundedAnswerResult,
)
from cinegraph.application.service.hybrid_grounded_answer_service import (
    HybridGroundedAnswerService,
)
from cinegraph.application.service.identity_session_service import (
    IdentitySessionService,
)
from cinegraph.bootstrap.composition_root import CinegraphCompositionRoot
from cinegraph.config import (
    DEFAULT_API_CONFIGURATION,
    CinegraphRuntimeSettings,
    OpenAISettings,
)
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest


class AnswerWorkflow(Protocol):
    def execute(
        self,
        query: HybridGroundedAnswerQuery,
    ) -> HybridGroundedAnswerResult: ...


@dataclass(slots=True)
class ApiContext:
    settings: CinegraphRuntimeSettings
    catalogue: CatalogueManifest
    identity_sessions: IdentitySessionService
    answer_workflow: AnswerWorkflow
    readiness_probe: Callable[[], bool]
    close_callback: Callable[[], None] = lambda: None

    def close(self) -> None:
        self.close_callback()


def build_default_api_context(env_file: Path = Path(".env")) -> ApiContext:
    settings = CinegraphRuntimeSettings(_env_file=env_file)
    openai = OpenAISettings(_env_file=env_file)
    loaded_catalogue = JsonCatalogueManifestLoader().load(
        settings.knowledge_root / DEFAULT_API_CONFIGURATION.catalogue_manifest_filename
    )
    root = CinegraphCompositionRoot(settings)
    chat_model = ChatOpenAI(
        model=openai.rag_answer_model,
        api_key=openai.openai_api_key.get_secret_value(),
        temperature=0,
    )
    answer_workflow = HybridGroundedAnswerGraphWorkflow(
        HybridGroundedAnswerService(
            root.hybrid_search_service,
            LangChainChatModelGateway.from_chat_model(chat_model),
        )
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
        close_callback=root.close,
    )
