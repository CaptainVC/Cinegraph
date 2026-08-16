from pathlib import Path

from cinegraph.bootstrap import CinegraphCompositionRoot
from cinegraph.config import CinegraphRuntimeSettings


def test_local_composition_provisions_idempotent_real_qdrant_schema(
    tmp_path: Path,
) -> None:
    settings = CinegraphRuntimeSettings(
        _env_file=None,
        qdrant_local_path=tmp_path / "qdrant",
        qdrant_collection_name="test_transcript_segments",
    )
    runtime = CinegraphCompositionRoot(settings)

    try:
        first = runtime.provision_transcript_collection()
        second = runtime.provision_transcript_collection()
    finally:
        runtime.close()

    assert first.collection_name == "test_transcript_segments"
    assert first.collection_created is True
    assert first.payload_indexes_created == ()
    assert second.collection_created is False
    assert second.payload_indexes_created == ()


def test_composition_root_lazily_reuses_injected_client_and_encoder() -> None:
    class FakeClient:
        def close(self) -> None:
            pass

    class FakeEncoder:
        pass

    client = FakeClient()
    encoder = FakeEncoder()
    client_calls = []
    encoder_calls = []

    def client_factory(settings):
        client_calls.append(settings)
        return client

    def encoder_factory():
        encoder_calls.append(True)
        return encoder

    settings = CinegraphRuntimeSettings(_env_file=None)
    runtime = CinegraphCompositionRoot(settings, client_factory, encoder_factory)

    assert runtime.qdrant_client is client
    assert runtime.qdrant_client is client
    assert runtime.vector_encoder is encoder
    assert runtime.vector_encoder is encoder
    assert runtime.hybrid_search_service is runtime.hybrid_search_service
    assert runtime.reviewed_corpus_ingestion_service is (
        runtime.reviewed_corpus_ingestion_service
    )
    assert client_calls == [settings]
    assert encoder_calls == [True]
