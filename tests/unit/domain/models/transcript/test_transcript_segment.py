from uuid import UUID

import pytest
from tests.factories import make_episode_ref

from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.transcript import SpeakerCandidate, TranscriptSegment
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef

SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
SEASON_ID = UUID("00000000-0000-0000-0000-000000000101")
EPISODE_ID = UUID("00000000-0000-0000-0000-000000001001")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000002001")
SEGMENT_ID = UUID("00000000-0000-0000-0000-000000003001")
SPEAKER_ID = UUID("00000000-0000-0000-0000-000000004001")
OTHER_SPEAKER_ID = UUID("00000000-0000-0000-0000-000000004002")


def episode() -> EpisodeRef:
    return make_episode_ref(
        series_id=SERIES_ID,
        season_id=SEASON_ID,
        episode_id=EPISODE_ID,
    )


def speaker_candidate(
    *,
    speaker_id: UUID = SPEAKER_ID,
    name: str = "CLAIRE",
    confidence: float = 1.0,
) -> SpeakerCandidate:
    return SpeakerCandidate(
        speaker_id=speaker_id,
        name=name,
        confidence=confidence,
    )


def make_segment(**overrides: object) -> TranscriptSegment:
    values: dict[str, object] = {
        "segment_id": SEGMENT_ID,
        "source_version_id": SOURCE_VERSION_ID,
        "episode": episode(),
        "start_ms": 1_000,
        "end_ms": 2_500,
        "text": "Hello there.",
        "language": Language.ENGLISH,
        "rights_status": RightsStatus.RESTRICTED,
        "style_removed": True,
        "speaker_candidates": (speaker_candidate(),),
    }
    values.update(overrides)
    return TranscriptSegment(**values)


def test_creates_a_canonical_transcript_segment() -> None:
    segment = make_segment()

    assert segment.start_ms == 1_000
    assert segment.end_ms == 2_500
    assert segment.speaker_candidates[0].name == "CLAIRE"
    assert segment.speaker_candidates[0].confidence == 1.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_ms": -1},
        {"start_ms": 2_500, "end_ms": 2_500},
        {"text": " Hello there."},
        {"language": "en"},
        {"speaker_candidates": [speaker_candidate()]},
    ],
)
def test_rejects_invalid_segment_invariants(overrides: dict[str, object]) -> None:
    with pytest.raises(InvalidModelError):
        make_segment(**overrides)


def test_rejects_duplicate_speaker_candidates_case_insensitively() -> None:
    with pytest.raises(InvalidModelError):
        make_segment(
            speaker_candidates=(
                speaker_candidate(name="Claire", confidence=1.0),
                speaker_candidate(
                    speaker_id=OTHER_SPEAKER_ID,
                    name="CLAIRE",
                    confidence=1.0,
                ),
            )
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan")])
def test_rejects_invalid_speaker_confidence(confidence: float) -> None:
    with pytest.raises(InvalidModelError):
        speaker_candidate(confidence=confidence)
