from uuid import UUID, uuid4

import pytest
from tests.factories import make_episode_ref

from cinegraph.application.service.transcript_chunking_service import TranscriptChunkingService
from cinegraph.common.error_messages import TranscriptErrorMessages
from cinegraph.config import TranscriptChunkingConfiguration
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.transcript import SpeakerCandidate, TranscriptSegment


def make_segments(count: int = 3) -> tuple[TranscriptSegment, ...]:
    source = UUID(int=700)
    episode = make_episode_ref()
    return tuple(
        TranscriptSegment(
            uuid4(),
            source,
            episode,
            index * 1_000,
            index * 1_000 + 500,
            f"Cue {index}",
            Language.ENGLISH,
            RightsStatus.ALLOWED,
            speaker_candidates=(SpeakerCandidate(UUID(int=900 + index), "Mira", 1.0),),
        )
        for index in range(count)
    )


def test_chunker_renders_speakers_and_deterministic_ids() -> None:
    service = TranscriptChunkingService(
        TranscriptChunkingConfiguration(max_segments=2, overlap_segments=1)
    )
    segments = make_segments()
    first = service.chunk(segments)
    second = service.chunk(segments)
    assert first[0].text == "Mira: Cue 0\nMira: Cue 1"
    assert first[0].member_segment_ids
    assert first[0].chunk_id != first[1].chunk_id
    assert first == second


def test_chunker_rejects_mixed_governance_before_chunking() -> None:
    segments = make_segments(2)
    mixed = TranscriptSegment(
        segments[1].segment_id,
        segments[1].source_version_id,
        segments[1].episode,
        segments[1].start_ms,
        segments[1].end_ms,
        segments[1].text,
        segments[1].language,
        RightsStatus.RESTRICTED,
    )
    with pytest.raises(InvalidModelError):
        TranscriptChunkingService().chunk((segments[0], mixed))


def test_chunker_breaks_large_scene_gaps_without_overlap() -> None:
    segments = make_segments(2)
    delayed = TranscriptSegment(
        segments[1].segment_id,
        segments[1].source_version_id,
        segments[1].episode,
        20_000,
        21_000,
        segments[1].text,
        segments[1].language,
        segments[1].rights_status,
    )
    chunks = TranscriptChunkingService().chunk((segments[0], delayed))
    assert len(chunks) == 2


def test_chunker_accepts_overlapping_cues_and_uses_the_latest_end_time() -> None:
    segments = make_segments(2)
    overlapping = TranscriptSegment(
        segments[1].segment_id,
        segments[1].source_version_id,
        segments[1].episode,
        250,
        1_250,
        segments[1].text,
        segments[1].language,
        segments[1].rights_status,
    )

    chunks = TranscriptChunkingService().chunk((segments[0], overlapping))

    assert len(chunks) == 1
    assert chunks[0].start_ms == 0
    assert chunks[0].end_ms == 1_250


def test_chunker_rejects_a_single_cue_that_exceeds_the_character_bound() -> None:
    segment = make_segments(1)[0]
    oversized = TranscriptSegment(
        segment.segment_id,
        segment.source_version_id,
        segment.episode,
        segment.start_ms,
        segment.end_ms,
        "x" * 11,
        segment.language,
        segment.rights_status,
    )
    service = TranscriptChunkingService(TranscriptChunkingConfiguration(max_characters=10))

    with pytest.raises(
        InvalidModelError,
        match=TranscriptErrorMessages.TRANSCRIPT_CHUNK_SEGMENT_EXCEEDS_CHARACTER_LIMIT,
    ):
        service.chunk((oversized,))
