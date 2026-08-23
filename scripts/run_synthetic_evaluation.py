"""Run a deterministic, in-memory Qdrant retrieval quality gate."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from qdrant_client import QdrantClient

from cinegraph.adapters.qdrant.qdrant_collection_provisioner import (
    QdrantTranscriptCollectionProvisioner,
)
from cinegraph.adapters.qdrant.qdrant_transcript_index_writer import QdrantTranscriptIndexWriter
from cinegraph.adapters.qdrant.qdrant_vector_index import QdrantVectorIndex
from cinegraph.application.models.index_transcript_segments import IndexTranscriptSegmentsCommand
from cinegraph.application.models.retrieval_evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluationDataset,
)
from cinegraph.application.service.index_transcript_segments_service import (
    IndexTranscriptSegmentsService,
)
from cinegraph.application.service.retrieval_evaluation_service import RetrievalEvaluationService
from cinegraph.application.service.search_visible_hybrid_segments_service import (
    SearchVisibleHybridSegmentsService,
)
from cinegraph.config import (
    DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA,
    HybridRetrievalConfiguration,
)
from cinegraph.config.qdrant import QdrantTranscriptCollectionSchema
from cinegraph.domain.enums.enum import (
    CorpusAccessMode,
    Language,
    RightsStatus,
    SourceAcquisitionMethod,
    SourceReviewStatus,
    SourceVersionStatus,
    SpoilerMode,
)
from cinegraph.domain.models.access import CorpusAccessScope, CorpusSeasonAccess
from cinegraph.domain.models.source import SourceVersion
from cinegraph.domain.models.transcript import SpeakerCandidate, TranscriptSegment
from cinegraph.domain.models.watch_state import (
    EpisodePosition,
    EpisodeRef,
    EpisodeWatchProgress,
    ProfileWatchState,
    SeriesWatchState,
)
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.domain.retrieval import RetrievalScopeCompiler
from cinegraph.domain.retrieval.vector_data import (
    DenseVector,
    DocumentVector,
    HybridVector,
    QueryVector,
    SparseVector,
)
from cinegraph.ports.retrieval.vector_encoder import VectorEncoder

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "synthetic_retrieval_evaluation.json"
)
SERIES_ID = UUID("00000000-0000-0000-0000-000000000025")


class SyntheticVectorEncoder(VectorEncoder):
    """Hash-only evaluation encoder; it is not available from production wiring."""

    def __init__(self, dimension: int = 8) -> None:
        self._dimension = dimension

    def _vector(self, text: str) -> HybridVector:
        dense = [0.0] * self._dimension
        indices: set[int] = set()
        for token in text.casefold().split():
            digest = hashlib.sha256(token.encode()).digest()
            dense[int.from_bytes(digest[:2], "big") % self._dimension] += 1.0
            indices.add(int.from_bytes(digest[2:6], "big"))
        norm = math.sqrt(sum(value * value for value in dense)) or 1.0
        sparse_indices = tuple(sorted(indices)) or (2_147_483_647,)
        return HybridVector(
            dense=DenseVector(tuple(value / norm for value in dense)),
            sparse=SparseVector(sparse_indices, tuple(1.0 for _ in sparse_indices)),
        )

    def encode_query(self, text: str) -> QueryVector:
        return QueryVector(self._vector(text))

    def encode_documents(self, texts: tuple[str, ...]) -> tuple[DocumentVector, ...]:
        return tuple(DocumentVector(self._vector(text)) for text in texts)

    def encode_document(self, text: str) -> DocumentVector:
        return self.encode_documents((text,))[0]


def _episode(season: int, number: int) -> EpisodeRef:
    return EpisodeRef(
        SERIES_ID,
        uuid5(NAMESPACE_URL, f"synthetic:season:{season}"),
        uuid5(NAMESPACE_URL, f"synthetic:episode:{season}:{number}"),
        EpisodePosition(season, number),
    )


def _source(episode: EpisodeRef) -> SourceVersion:
    source_id = uuid5(NAMESPACE_URL, f"synthetic:source:{episode.episode_id}")
    now = datetime.now(timezone.utc)
    return SourceVersion(
        source_id,
        uuid5(NAMESPACE_URL, f"synthetic:document:{episode.episode_id}"),
        hashlib.sha256(str(source_id).encode()).hexdigest(),
        RightsStatus.ALLOWED,
        SourceAcquisitionMethod.SYNTHETIC_FIXTURE,
        SourceReviewStatus.REVIEWED,
        SourceVersionStatus.ACTIVE,
        now,
        reviewed_by="synthetic-evaluator",
        reviewed_at=now,
    )


def _segments(
    episode: EpisodeRef, source: SourceVersion, cues: tuple[tuple[int, int, str, str | None], ...]
) -> tuple[TranscriptSegment, ...]:
    return tuple(
        TranscriptSegment(
            uuid5(NAMESPACE_URL, f"synthetic:cue:{episode.episode_id}:{index}"),
            source.source_version_id,
            episode,
            start,
            end,
            text,
            Language.ENGLISH,
            RightsStatus.ALLOWED,
            speaker_candidates=(
                (
                    SpeakerCandidate(
                        uuid5(NAMESPACE_URL, f"synthetic:speaker:{speaker}"), speaker, 1.0
                    ),
                )
                if speaker
                else ()
            ),
        )
        for index, (start, end, text, speaker) in enumerate(cues)
    )


def main() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    schema = QdrantTranscriptCollectionSchema(
        "synthetic_transcript_segments",
        "dense",
        "sparse",
        8,
        DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA.distance,
        False,
        (),
    )
    client = QdrantClient(":memory:")
    try:
        QdrantTranscriptCollectionProvisioner(client, schema).provision()
        encoder = SyntheticVectorEncoder()
        indexer = IndexTranscriptSegmentsService(
            encoder, QdrantTranscriptIndexWriter(client, schema)
        )
        fixture_cues = {
            (1, 1): (
                (0, 900, "Mira studies the glass harbor map.", "Mira"),
                (
                    900,
                    1800,
                    "She discovers a brass compass beneath the glass harbor steps.",
                    "Mira",
                ),
            ),
            (1, 2): ((0, 900, "The glass harbor contains a tempting brass compass.", "Tomas"),),
            (1, 3): ((0, 900, "Tomas repairs the clockwork garden gate before sunrise.", "Tomas"),),
            (2, 1): (
                (0, 900, "The unauthorized archive repeats the glass harbor clue.", "Archivist"),
            ),
            (1, 4): (
                (0, 1000, "Mira enters the quiet lantern room.", "Mira"),
                (1600, 2400, "The hidden lantern mechanism turns.", "Mira"),
            ),
        }
        refs: dict[tuple[int, int], EpisodeRef] = {}
        for position, cues in fixture_cues.items():
            episode = _episode(*position)
            source = _source(episode)
            refs[position] = episode
            indexer.execute(
                IndexTranscriptSegmentsCommand(source, _segments(episode, source, cues))
            )
        vector_index = QdrantVectorIndex(
            client, schema, HybridRetrievalConfiguration(max_requested_result_limit=10)
        )
        search = SearchVisibleHybridSegmentsService(
            RetrievalScopeCompiler(SpoilerPolicy()), encoder, vector_index
        )
        unrestricted = CorpusAccessScope(
            CorpusAccessMode.AUTHENTICATED, "synthetic-v2", frozenset(), unrestricted=True
        )
        guest = CorpusAccessScope(
            CorpusAccessMode.GUEST, "synthetic-v2", frozenset({CorpusSeasonAccess(SERIES_ID, 1)})
        )
        partial_state = ProfileWatchState(
            uuid5(NAMESPACE_URL, "synthetic:profile"),
            "Synthetic evaluator",
            (SeriesWatchState(SERIES_ID, (EpisodeWatchProgress(refs[(1, 4)], False, 1500),)),),
            SpoilerMode.STRICT,
        )
        cases = []
        for item in fixture["cases"]:
            positions = tuple(tuple(position) for position in item["candidate_positions"])
            cases.append(
                RetrievalEvaluationCase(
                    item["case_id"],
                    item["query"],
                    SERIES_ID,
                    tuple(refs[position] for position in positions),
                    frozenset({refs[tuple(item["expected_position"])].episode_id}),
                    frozenset(
                        refs[position].episode_id
                        for position in (
                            tuple(position) for position in item["forbidden_positions"]
                        )
                    ),
                    guest if item["access"] == "guest" else unrestricted,
                    item.get("limit", 2),
                    partial_state if item.get("watch_state") == "partial" else None,
                )
            )
        report = RetrievalEvaluationService(search).execute(
            RetrievalEvaluationDataset(fixture["schema_version"], tuple(cases))
        )
        output = {
            "case_count": len(report.case_results),
            "case_ids": [result.case_id for result in report.case_results],
            "hit_rate": report.hit_rate,
            "mean_reciprocal_rank": report.mean_reciprocal_rank,
            "mean_recall_at_k": report.mean_recall_at_k,
            "mean_ndcg_at_k": report.mean_ndcg_at_k,
            "forbidden_episode_leak_count": report.forbidden_episode_leak_count,
            "passed": report.passed,
        }
        print(json.dumps(output, sort_keys=True))
        if not report.passed:
            raise SystemExit("Synthetic retrieval evaluation failed")
    finally:
        client.close()


if __name__ == "__main__":
    main()
