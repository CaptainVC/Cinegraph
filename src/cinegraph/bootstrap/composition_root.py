from collections.abc import Callable
from dataclasses import replace
from functools import cached_property

from qdrant_client import QdrantClient

from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.adapters.ingestion.finalized_srt_canonicalizer import (
    FinalizedSrtCanonicalizer,
)
from cinegraph.adapters.qdrant.qdrant_collection_provisioner import (
    QdrantCollectionProvisioningResult,
    QdrantTranscriptCollectionProvisioner,
)
from cinegraph.adapters.qdrant.qdrant_transcript_index_writer import (
    QdrantTranscriptIndexWriter,
)
from cinegraph.adapters.qdrant.qdrant_vector_index import QdrantVectorIndex
from cinegraph.adapters.repository.in_memory.in_memory_transcript_ingestion_repository import (
    InMemoryTranscriptIngestionRepository,
)
from cinegraph.adapters.retrieval.fastembed_vector_encoder import (
    FastEmbedVectorEncoder,
)
from cinegraph.adapters.source.local_subtitle_text_reader import (
    LocalSubtitleTextReader,
)
from cinegraph.application.service.index_transcript_segments_service import (
    IndexTranscriptSegmentsService,
)
from cinegraph.application.service.ingest_reviewed_corpus_service import (
    IngestReviewedCorpusService,
)
from cinegraph.application.service.ingest_reviewed_subtitle_service import (
    IngestReviewedSubtitleService,
)
from cinegraph.application.service.search_visible_hybrid_segments_service import (
    SearchVisibleHybridSegmentsService,
)
from cinegraph.config import (
    DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA,
    CinegraphRuntimeSettings,
    QdrantRuntimeMode,
    QdrantTranscriptCollectionSchema,
)
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.domain.retrieval import RetrievalScopeCompiler
from cinegraph.ports.retrieval import VectorEncoder


QdrantClientFactory = Callable[[CinegraphRuntimeSettings], QdrantClient]
VectorEncoderFactory = Callable[[], VectorEncoder]


def _default_qdrant_client_factory(
    settings: CinegraphRuntimeSettings,
) -> QdrantClient:
    if settings.qdrant_mode is QdrantRuntimeMode.LOCAL:
        return QdrantClient(path=str(settings.qdrant_local_path))
    api_key = (
        settings.qdrant_api_key.get_secret_value()
        if settings.qdrant_api_key is not None
        else None
    )
    return QdrantClient(url=str(settings.qdrant_url), api_key=api_key)


class CinegraphCompositionRoot:
    # Lazily own shared runtime dependencies so model loads and clients occur once.
    def __init__(
        self,
        settings: CinegraphRuntimeSettings,
        qdrant_client_factory: QdrantClientFactory = _default_qdrant_client_factory,
        vector_encoder_factory: VectorEncoderFactory = (
            FastEmbedVectorEncoder.from_default_models
        ),
    ) -> None:
        self.settings = settings
        self._qdrant_client_factory = qdrant_client_factory
        self._vector_encoder_factory = vector_encoder_factory

    @cached_property
    def qdrant_schema(self) -> QdrantTranscriptCollectionSchema:
        return replace(
            DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA,
            collection_name=self.settings.qdrant_collection_name,
            payload_indexes=(
                ()
                if self.settings.qdrant_mode is QdrantRuntimeMode.LOCAL
                else DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA.payload_indexes
            ),
        )

    @cached_property
    def qdrant_client(self) -> QdrantClient:
        return self._qdrant_client_factory(self.settings)

    @cached_property
    def vector_encoder(self) -> VectorEncoder:
        return self._vector_encoder_factory()

    @cached_property
    def hybrid_search_service(self) -> SearchVisibleHybridSegmentsService:
        return SearchVisibleHybridSegmentsService(
            RetrievalScopeCompiler(SpoilerPolicy()),
            self.vector_encoder,
            QdrantVectorIndex(
                self.qdrant_client,
                self.qdrant_schema.collection_name,
            ),
        )

    @cached_property
    def reviewed_corpus_ingestion_service(self) -> IngestReviewedCorpusService:
        repository = InMemoryTranscriptIngestionRepository()
        return IngestReviewedCorpusService(
            IngestReviewedSubtitleService(
                repository,
                LocalSubtitleTextReader(),
                FinalizedSrtCanonicalizer(),
                SystemClock(),
            ),
            IndexTranscriptSegmentsService(
                self.vector_encoder,
                QdrantTranscriptIndexWriter(
                    self.qdrant_client,
                    self.qdrant_schema.collection_name,
                ),
            ),
        )

    def provision_transcript_collection(
        self,
    ) -> QdrantCollectionProvisioningResult:
        return QdrantTranscriptCollectionProvisioner(
            self.qdrant_client,
            self.qdrant_schema,
        ).provision()

    def close(self) -> None:
        if "qdrant_client" in self.__dict__:
            self.qdrant_client.close()
