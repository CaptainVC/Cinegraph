from dataclasses import replace
from uuid import UUID

import pytest
from tests.factories import make_episode_ref

from cinegraph.common.error_messages import TranscriptErrorMessages
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.transcript import TranscriptRetrievalChunk


def make_chunk() -> TranscriptRetrievalChunk:
    return TranscriptRetrievalChunk(
        chunk_id=UUID(int=1),
        source_version_id=UUID(int=2),
        episode=make_episode_ref(),
        ordinal=0,
        member_segment_ids=(UUID(int=3),),
        start_ms=1_000,
        end_ms=2_000,
        text="Mira: Relevant evidence.",
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
        index_revision=TRANSCRIPT_INDEX_REVISION,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"chunk_id": "not-a-uuid"},
        {"source_version_id": "not-a-uuid"},
        {"episode": "not-an-episode"},
        {"language": "not-a-language"},
        {"rights_status": "not-a-rights-status"},
        {"index_revision": " obsolete"},
    ],
)
def test_chunk_rejects_invalid_source_and_governance_fields(
    changes: dict[str, object],
) -> None:
    with pytest.raises(
        InvalidModelError,
        match=TranscriptErrorMessages.TRANSCRIPT_CHUNK_SOURCE_FIELDS_MUST_BE_VALID,
    ):
        replace(make_chunk(), **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "member_segment_ids",
    [(), (UUID(int=3), UUID(int=3)), [UUID(int=3)], ("not-a-uuid",)],
)
def test_chunk_requires_nonempty_unique_immutable_member_ids(
    member_segment_ids: object,
) -> None:
    with pytest.raises(
        InvalidModelError,
        match=TranscriptErrorMessages.TRANSCRIPT_CHUNK_SEGMENTS_MUST_BE_NONEMPTY_UNIQUE,
    ):
        replace(  # type: ignore[arg-type]
            make_chunk(),
            member_segment_ids=member_segment_ids,
        )


@pytest.mark.parametrize("ordinal", [True, -1, 1.0, "1"])
def test_chunk_requires_a_nonnegative_integer_ordinal(ordinal: object) -> None:
    with pytest.raises(
        InvalidModelError,
        match=TranscriptErrorMessages.TRANSCRIPT_CHUNK_ORDINAL_MUST_BE_NON_NEGATIVE,
    ):
        replace(make_chunk(), ordinal=ordinal)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"start_ms": True},
        {"start_ms": -1},
        {"end_ms": 1_000},
        {"end_ms": 2_000.0},
    ],
)
def test_chunk_requires_valid_integer_timing(changes: dict[str, object]) -> None:
    with pytest.raises(
        InvalidModelError,
        match=TranscriptErrorMessages.TRANSCRIPT_CHUNK_TIMING_MUST_BE_VALID,
    ):
        replace(make_chunk(), **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["", " leading", "trailing ", None])
def test_chunk_requires_trimmed_nonempty_text(text: object) -> None:
    with pytest.raises(
        InvalidModelError,
        match=TranscriptErrorMessages.TRANSCRIPT_CHUNK_TEXT_MUST_BE_TRIMMED,
    ):
        replace(make_chunk(), text=text)  # type: ignore[arg-type]
