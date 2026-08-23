from uuid import UUID

from tests.factories import make_episode_ref

from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment
from cinegraph.domain.retrieval.lexical import lexical_score, normalize_tokens

SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000501")


def segment(text: str) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=UUID("00000000-0000-0000-0000-000000000901"),
        source_version_id=SOURCE_VERSION_ID,
        episode=make_episode_ref(),
        start_ms=1_000,
        end_ms=2_000,
        text=text,
        language=Language.ENGLISH,
        rights_status=RightsStatus.RESTRICTED,
    )


def test_normalize_tokens_deduplicates_and_casefolds_text() -> None:
    assert normalize_tokens("Luke, LUKE! 123") == frozenset({"luke", "123"})


def test_exactly_matching_terms_score_higher_than_partial_match() -> None:
    exact_match = segment("Luke got his head stuck in the banister again.")
    partial_match = segment("Luke is having breakfast with his family.")

    query = "Luke stuck banister"

    assert lexical_score(query, exact_match) > lexical_score(query, partial_match)


def test_blank_query_scores_zero() -> None:
    assert lexical_score("", segment("Any transcript line.")) == 0.0


def test_non_matching_query_scores_zero() -> None:
    assert lexical_score("roller coaster", segment("Dinner is ready.")) == 0.0
