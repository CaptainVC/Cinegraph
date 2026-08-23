from dataclasses import replace
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
from cinegraph.common.error_messages import GraphErrorMessages
from cinegraph.config.graph_claims import (
    GraphClaimExtractionConfiguration,
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
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.transcript.transcript_retrieval_chunk import TranscriptRetrievalChunk
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodePosition, EpisodeRef


class _Extractor:
    def __init__(self, outputs: tuple[object, ...] = ()) -> None:
        self.outputs = outputs
        self.batches: list[tuple[TranscriptRetrievalChunk, ...]] = []

    def extract(self, chunks: tuple[TranscriptRetrievalChunk, ...]) -> object:
        self.batches.append(chunks)
        index = len(self.batches) - 1
        output = self.outputs[index] if index < len(self.outputs) else ()
        return (output,) if isinstance(output, ExtractedGraphClaim) else output


class _Store:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def replace_source_version(self, *args: object) -> None:
        self.calls.append(args)


def _source(
    *,
    rights_status: RightsStatus = RightsStatus.ALLOWED,
    status: SourceVersionStatus = SourceVersionStatus.ACTIVE,
    review_status: SourceReviewStatus = SourceReviewStatus.AUTOMATED_REVIEWED,
    parent: UUID | None = None,
) -> SourceVersion:
    return SourceVersion(
        uuid4(),
        uuid4(),
        "a" * 64,
        rights_status,
        SourceAcquisitionMethod.SYNTHETIC_FIXTURE,
        review_status,
        status,
        datetime(2026, 8, 23, tzinfo=UTC),
        parent_source_version_id=parent,
        reviewed_by="automated",
        reviewed_at=datetime(2026, 8, 23, tzinfo=UTC),
    )


def _chunk(
    source_id: UUID,
    series_id: UUID,
    ordinal: int,
    *,
    rights_status: RightsStatus = RightsStatus.ALLOWED,
    revision: str = TRANSCRIPT_INDEX_REVISION,
) -> TranscriptRetrievalChunk:
    return TranscriptRetrievalChunk(
        uuid4(),
        source_id,
        EpisodeRef(series_id, uuid4(), uuid4(), EpisodePosition(1, ordinal + 1)),
        ordinal,
        (uuid4(),),
        ordinal * 1000,
        ordinal * 1000 + 900,
        f"cue {ordinal}",
        Language.ENGLISH,
        rights_status,
        revision,
    )


def _candidate(
    chunk_ids: tuple[UUID, ...],
    *,
    subject: str = "Alex",
    object_name: str = "Sam",
    confidence: float = 0.5,
    subject_aliases: tuple[str, ...] = (),
    object_aliases: tuple[str, ...] = (),
) -> ExtractedGraphClaim:
    return ExtractedGraphClaim(
        ExtractedEntityReference(GraphEntityKind.CHARACTER, subject, subject_aliases),
        "knows",
        ExtractedEntityReference(GraphEntityKind.PERSON, object_name, object_aliases),
        GraphClaimPolarity.ASSERTED,
        confidence,
        chunk_ids,
    )


def test_service_batches_in_order_and_rejects_cross_batch_evidence() -> None:
    source = _source()
    series_id = uuid4()
    first, second = (
        _chunk(source.source_version_id, series_id, 0),
        _chunk(source.source_version_id, series_id, 1),
    )
    extractor = _Extractor((_candidate((second.chunk_id,)), ()))
    store = _Store()
    service = ExtractAndReplaceGraphClaimsService(
        extractor,
        store,
        GraphClaimExtractionConfiguration(batch_size=1),
    )
    with pytest.raises(InvalidModelError, match=GraphErrorMessages.UNKNOWN_EVIDENCE):
        service.execute(ExtractAndReplaceGraphClaimsCommand(source, (first, second)))
    assert [batch[0].chunk_id for batch in extractor.batches] == [first.chunk_id]
    assert store.calls == []


def test_service_batches_valid_candidates_in_input_order() -> None:
    source = _source()
    series_id = uuid4()
    first = _chunk(source.source_version_id, series_id, 0)
    second = _chunk(source.source_version_id, series_id, 1)
    extractor = _Extractor((_candidate((first.chunk_id,)), _candidate((second.chunk_id,))))
    store = _Store()
    result = ExtractAndReplaceGraphClaimsService(
        extractor,
        store,
        GraphClaimExtractionConfiguration(batch_size=1),
    ).execute(ExtractAndReplaceGraphClaimsCommand(source, (first, second)))
    assert [batch[0].chunk_id for batch in extractor.batches] == [first.chunk_id, second.chunk_id]
    assert result.input_chunk_count == result.candidate_count == 2
    assert len(store.calls) == 1


def test_service_produces_stable_ids_merges_claims_and_max_confidence() -> None:
    source = _source()
    chunk = _chunk(source.source_version_id, uuid4(), 0)
    first = _candidate((chunk.chunk_id,), confidence=0.2, subject_aliases=("A",))
    second = _candidate((chunk.chunk_id,), confidence=0.9, subject="alex", subject_aliases=("Al",))
    first_store, second_store = _Store(), _Store()
    service = ExtractAndReplaceGraphClaimsService(_Extractor(((first, second),)), first_store)
    result = service.execute(ExtractAndReplaceGraphClaimsCommand(source, (chunk,)))
    again = ExtractAndReplaceGraphClaimsService(_Extractor(((second, first),)), second_store)
    again.execute(ExtractAndReplaceGraphClaimsCommand(source, (chunk,)))
    assert result.candidate_count == 2
    assert result.claim_count == result.evidence_count == 1
    assert first_store.calls[0][3] == second_store.calls[0][3]
    assert first_store.calls[0][4] == second_store.calls[0][4]
    evidence = first_store.calls[0][4][0]
    assert evidence.confidence == 0.9
    subject = next(
        entity for entity in first_store.calls[0][2] if entity.kind is GraphEntityKind.CHARACTER
    )
    assert subject.display_name == "Alex"
    assert subject.aliases == ("A", "Al", "Alex")


def test_service_rejects_invalid_command_source_chunks_without_side_effects() -> None:
    source = _source()
    series_id = uuid4()
    valid = _chunk(source.source_version_id, series_id, 0)
    extractor, store = _Extractor((_candidate((valid.chunk_id,)),)), _Store()
    service = ExtractAndReplaceGraphClaimsService(extractor, store)
    with pytest.raises(InvalidModelError, match=GraphErrorMessages.COMMAND_INVALID):
        service.execute(object())  # type: ignore[arg-type]
    for invalid_source in (
        replace(source, rights_status=RightsStatus.RESTRICTED),
        replace(source, status=SourceVersionStatus.RETIRED),
        replace(
            source, review_status=SourceReviewStatus.PENDING, reviewed_by=None, reviewed_at=None
        ),
    ):
        with pytest.raises(InvalidModelError):
            service.execute(ExtractAndReplaceGraphClaimsCommand(invalid_source, (valid,)))
    invalid_chunk = replace(valid, source_version_id=uuid4())
    with pytest.raises(InvalidModelError, match=GraphErrorMessages.CHUNKS_INVALID):
        service.execute(ExtractAndReplaceGraphClaimsCommand(source, (invalid_chunk,)))
    assert extractor.batches == []
    assert store.calls == []


def test_service_rejects_duplicate_series_revision_and_rights_chunks() -> None:
    source = _source()
    series_id = uuid4()
    chunk = _chunk(source.source_version_id, series_id, 0)
    service = ExtractAndReplaceGraphClaimsService(_Extractor(), _Store())
    for chunks in (
        (chunk, chunk),
        (chunk, _chunk(source.source_version_id, uuid4(), 1)),
        (
            chunk,
            replace(
                _chunk(source.source_version_id, series_id, 1),
                rights_status=RightsStatus.RESTRICTED,
            ),
        ),
        (chunk, replace(_chunk(source.source_version_id, series_id, 1), index_revision="old")),
    ):
        with pytest.raises(InvalidModelError, match=GraphErrorMessages.CHUNKS_INVALID):
            service.execute(ExtractAndReplaceGraphClaimsCommand(source, chunks))


def test_service_rejects_non_tuple_and_overflow_extractor_output() -> None:
    source = _source()
    chunk = _chunk(source.source_version_id, uuid4(), 0)
    non_tuple = ExtractAndReplaceGraphClaimsService(
        _Extractor(([_candidate((chunk.chunk_id,))],)), _Store()
    )
    with pytest.raises(InvalidModelError, match=GraphErrorMessages.BATCH_OUTPUT_INVALID):
        non_tuple.execute(ExtractAndReplaceGraphClaimsCommand(source, (chunk,)))
    series_id = uuid4()
    first, second = (
        _chunk(source.source_version_id, series_id, 0),
        _chunk(source.source_version_id, series_id, 1),
    )
    overflow = ExtractAndReplaceGraphClaimsService(
        _Extractor((_candidate((first.chunk_id,)), _candidate((second.chunk_id,)))),
        _Store(),
        GraphClaimExtractionConfiguration(batch_size=1, max_candidates=1),
    )
    with pytest.raises(InvalidModelError, match=GraphErrorMessages.BATCH_OUTPUT_INVALID):
        overflow.execute(ExtractAndReplaceGraphClaimsCommand(source, (first, second)))


def test_service_rejects_alias_limits_and_empty_replacement_is_store_only() -> None:
    source = _source(parent=uuid4())
    chunk = _chunk(source.source_version_id, uuid4(), 0)
    too_many = _candidate((chunk.chunk_id,), subject_aliases=("A", "B", "C"))
    extractor, store = _Extractor((too_many,)), _Store()
    service = ExtractAndReplaceGraphClaimsService(
        extractor,
        store,
        GraphClaimExtractionConfiguration(max_aliases=2),
    )
    with pytest.raises(InvalidModelError, match=GraphErrorMessages.ENTITY_ALIASES_INVALID):
        service.execute(ExtractAndReplaceGraphClaimsCommand(source, (chunk,)))
    empty_extractor, empty_store = _Extractor(), _Store()
    empty = ExtractAndReplaceGraphClaimsService(empty_extractor, empty_store)
    result = empty.execute(ExtractAndReplaceGraphClaimsCommand(source, ()))
    assert result.input_chunk_count == 0
    assert empty_extractor.batches == []
    assert len(empty_store.calls) == 1
