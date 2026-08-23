from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from cinegraph.application.models.graph_claim_extraction import (
    ExtractAndReplaceGraphClaimsCommand,
    ExtractedEntityReference,
    ExtractedGraphClaim,
)
from cinegraph.application.service.extract_and_replace_graph_claims_service import (
    ExtractAndReplaceGraphClaimsService,
)
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    GraphClaimPolarity,
    GraphEntityKind,
    Language,
    RightsStatus,
    SourceAcquisitionMethod,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.transcript.transcript_retrieval_chunk import TranscriptRetrievalChunk
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodePosition, EpisodeRef


class _Extractor:
    def __init__(self, claim: ExtractedGraphClaim | None) -> None:
        self.claim = claim
        self.calls: list[tuple[object, ...]] = []

    def extract(self, chunks: tuple[TranscriptRetrievalChunk, ...]) -> tuple[ExtractedGraphClaim, ...]:
        self.calls.append(chunks)
        return () if self.claim is None else (self.claim,)


class _Store:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def replace_source_version(self, *args: object) -> None:
        self.calls.append(args)


def _source(parent: object = None) -> SourceVersion:
    return SourceVersion(uuid4(), uuid4(), "a" * 64, RightsStatus.ALLOWED, SourceAcquisitionMethod.SYNTHETIC_FIXTURE, SourceReviewStatus.AUTOMATED_REVIEWED, SourceVersionStatus.ACTIVE, datetime.now(UTC), parent_source_version_id=parent if isinstance(parent, UUID) else None, reviewed_by="automated", reviewed_at=datetime.now(UTC))


def _chunk(source_id: object) -> TranscriptRetrievalChunk:
    series, season, episode = uuid4(), uuid4(), uuid4()
    return TranscriptRetrievalChunk(uuid4(), source_id, EpisodeRef(series, season, episode, EpisodePosition(1, 1)), 0, (uuid4(),), 0, 1000, "hello", Language.ENGLISH, RightsStatus.ALLOWED, TRANSCRIPT_INDEX_REVISION)


def test_extract_merge_and_replace_is_grounded_and_stable() -> None:
    source = _source()
    chunk = _chunk(source.source_version_id)
    claim = ExtractedGraphClaim(ExtractedEntityReference(GraphEntityKind.CHARACTER, "Alex", ("ALEX",)), "knows", ExtractedEntityReference(GraphEntityKind.PERSON, "Sam"), GraphClaimPolarity.ASSERTED, 0.8, (chunk.chunk_id,))
    extractor, store = _Extractor(claim), _Store()
    service = ExtractAndReplaceGraphClaimsService(extractor, store)
    result = service.execute(ExtractAndReplaceGraphClaimsCommand(source, (chunk,)))
    assert (result.input_chunk_count, result.candidate_count, result.entity_count, result.claim_count, result.evidence_count) == (1, 1, 2, 1, 1)
    assert len(store.calls) == 1
    entity_aliases = [entity.aliases for entity in store.calls[0][2]]
    assert any("Alex" in aliases for aliases in entity_aliases)


def test_empty_replacement_does_not_call_extractor() -> None:
    parent = uuid4()
    source = _source(parent)
    extractor, store = _Extractor(None), _Store()
    result = ExtractAndReplaceGraphClaimsService(extractor, store).execute(ExtractAndReplaceGraphClaimsCommand(source, ()))
    assert result.input_chunk_count == 0
    assert extractor.calls == []
    assert len(store.calls) == 1


def test_current_transcript_revision_is_required() -> None:
    source = _source()
    chunk = _chunk(source.source_version_id)
    invalid = TranscriptRetrievalChunk(chunk.chunk_id, chunk.source_version_id, chunk.episode, chunk.ordinal, chunk.member_segment_ids, chunk.start_ms, chunk.end_ms, chunk.text, chunk.language, chunk.rights_status, "old-revision")
    with pytest.raises(ValueError):
        ExtractAndReplaceGraphClaimsService(_Extractor(None), _Store()).execute(ExtractAndReplaceGraphClaimsCommand(source, (invalid,)))
