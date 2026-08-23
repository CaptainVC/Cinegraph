from uuid import UUID

from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.domain.enums.enum import Language

SOURCE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000401")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000501")
SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
EPISODE_ID = UUID("00000000-0000-0000-0000-000000001001")
CONTENT_HASH = "a" * 64


def test_generates_distinct_random_ids() -> None:
    assert IdentifierGenerator.new_id() != IdentifierGenerator.new_id()


def test_generates_deterministic_source_version_id() -> None:
    first_id = IdentifierGenerator.source_version_id(
        SOURCE_DOCUMENT_ID,
        CONTENT_HASH,
    )
    second_id = IdentifierGenerator.source_version_id(
        SOURCE_DOCUMENT_ID,
        CONTENT_HASH,
    )

    assert first_id == second_id
    assert first_id != IdentifierGenerator.source_version_id(
        SOURCE_DOCUMENT_ID,
        "b" * 64,
    )


def test_generates_case_insensitive_deterministic_speaker_id() -> None:
    assert IdentifierGenerator.speaker_id(
        SERIES_ID,
        "Claire",
    ) == IdentifierGenerator.speaker_id(SERIES_ID, "CLAIRE")


def test_generates_deterministic_transcript_segment_id() -> None:
    first_id = IdentifierGenerator.transcript_segment_id(
        SOURCE_VERSION_ID,
        EPISODE_ID,
        1,
        1_000,
        2_000,
        "Hello there.",
    )
    second_id = IdentifierGenerator.transcript_segment_id(
        SOURCE_VERSION_ID,
        EPISODE_ID,
        1,
        1_000,
        2_000,
        "Hello there.",
    )

    assert first_id == second_id
    assert first_id != IdentifierGenerator.transcript_segment_id(
        SOURCE_VERSION_ID,
        EPISODE_ID,
        1,
        1_000,
        2_000,
        "Different dialogue.",
    )


def test_generates_case_insensitive_episode_summary_source_document_id() -> None:
    first_id = IdentifierGenerator.episode_summary_source_document_id(
        EPISODE_ID,
        Language.ENGLISH,
        "Wikipedia",
    )
    second_id = IdentifierGenerator.episode_summary_source_document_id(
        EPISODE_ID,
        Language.ENGLISH,
        "wikipedia",
    )

    assert first_id == second_id
    assert first_id != IdentifierGenerator.episode_summary_source_document_id(
        EPISODE_ID,
        Language.ENGLISH,
        "another-provider",
    )
