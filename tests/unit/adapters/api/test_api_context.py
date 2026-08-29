from pathlib import Path

from tests.unit.adapters.api.test_fastapi_app import make_catalogue

import cinegraph.adapters.api.context as context_module
from cinegraph.ports.catalogue import LoadedCatalogueManifest


class FakeCompositionRoot:
    instances = []

    def __init__(self, settings) -> None:
        self.settings = settings
        self.hybrid_search_service = object()
        self.transcript_vector_index = object()
        self.identity_engine = object()
        self.identity_session_service = object()
        self.closed = 0
        self.__class__.instances.append(self)

    def close(self) -> None:
        self.closed += 1


class FakeSeriesResearchAgent:
    instances = []

    def __init__(
        self,
        model,
        transcript_workflow,
        graph_rag_service,
        *,
        checkpointer,
        tool_selector_model,
    ) -> None:
        self.model = model
        self.transcript_workflow = transcript_workflow
        self.graph_rag_service = graph_rag_service
        self.checkpointer = checkpointer
        self.tool_selector_model = tool_selector_model
        self.__class__.instances.append(self)

    def invoke(self, question, context, thread_id):
        raise AssertionError("The composition smoke test must not invoke a provider.")


def test_default_context_wires_bounded_private_agent_models(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=local-composition-smoke-test\n"
        f"CINEGRAPH_KNOWLEDGE_ROOT={(tmp_path / 'knowledge').as_posix()}\n",
        encoding="utf-8",
    )
    model_calls = []

    def fake_chat_openai(**kwargs):
        model_calls.append(kwargs)
        return object()

    monkeypatch.setattr(context_module, "ChatOpenAI", fake_chat_openai)
    monkeypatch.setattr(context_module, "CinegraphCompositionRoot", FakeCompositionRoot)
    monkeypatch.setattr(context_module, "SeriesResearchAgent", FakeSeriesResearchAgent)
    monkeypatch.setattr(
        context_module,
        "SqlAlchemyGraphClaimReader",
        lambda _engine: object(),
    )
    monkeypatch.setattr(
        context_module.JsonCatalogueManifestLoader,
        "load",
        lambda _self, _path: LoadedCatalogueManifest(
            manifest=make_catalogue(), content_sha256="a" * 64
        ),
    )
    monkeypatch.setattr(
        context_module.LangChainChatModelGateway,
        "from_chat_model",
        classmethod(lambda _cls, _model: object()),
    )
    monkeypatch.setattr(
        context_module.LangChainEpisodeRecommendationRanker,
        "from_chat_model",
        classmethod(lambda _cls, _model: object()),
    )

    context = context_module.build_default_api_context(env_file)

    assert context.agent_job_service is not None
    assert len(model_calls) == 4
    synthesis, selector = model_calls[-2:]
    assert synthesis["model"] == "gpt-5.6-terra"
    assert selector["model"] == "gpt-5.6-luna"
    for call in (synthesis, selector):
        assert call["store"] is False
        assert call["max_retries"] == 0
        assert call["timeout"] == 60.0
    assert FakeSeriesResearchAgent.instances[-1].model is not None
    assert FakeSeriesResearchAgent.instances[-1].tool_selector_model is not None

    root = FakeCompositionRoot.instances[-1]
    context.close()
    context.close()
    assert root.closed == 1
