from datetime import UTC, datetime
from uuid import UUID

import pytest

from cinegraph.domain.enums.enum import (
    RightsStatus,
    SourceAcquisitionMethod,
    SourceKind,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.source import SourceDocument, SourceVersion


SOURCE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000401")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000501")
PARENT_SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000502")
ACQUIRED_AT = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 7, 19, 13, 0, tzinfo=UTC)
CONTENT_HASH = "a" * 64


def make_source_document(**overrides: object) -> SourceDocument:
    values: dict[str, object] = {
        "source_document_id": SOURCE_DOCUMENT_ID,
        "title": "Modern Family S01E01 reviewed subtitle",
        "kind": SourceKind.SUBTITLE,
        "origin": "private-local-corpus",
    }
    values.update(overrides)
    return SourceDocument(**values)


def make_source_version(**overrides: object) -> SourceVersion:
    values: dict[str, object] = {
        "source_version_id": SOURCE_VERSION_ID,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "content_hash": CONTENT_HASH,
        "rights_status": RightsStatus.RESTRICTED,
        "acquisition_method": SourceAcquisitionMethod.LOCAL_FILESYSTEM,
        "review_status": SourceReviewStatus.REVIEWED,
        "status": SourceVersionStatus.ACTIVE,
        "acquired_at": ACQUIRED_AT,
        "reviewed_by": "local-corpus-owner",
        "reviewed_at": REVIEWED_AT,
    }
    values.update(overrides)
    return SourceVersion(**values)


def test_creates_reviewed_private_subtitle_source_lifecycle() -> None:
    source_document = make_source_document()
    source_version = make_source_version()

    assert source_document.kind is SourceKind.SUBTITLE
    assert source_version.source_document_id == source_document.source_document_id
    assert source_version.review_status is SourceReviewStatus.REVIEWED
    assert source_version.status is SourceVersionStatus.ACTIVE
    assert source_version.content_hash == CONTENT_HASH


@pytest.mark.parametrize(
    "content_hash",
    [
        "abc",
        "A" * 64,
        "g" * 64,
    ],
)
def test_rejects_invalid_sha256_content_hash(content_hash: str) -> None:
    with pytest.raises(InvalidModelError):
        make_source_version(content_hash=content_hash)


@pytest.mark.parametrize(
    "overrides",
    [
        {"reviewed_by": None},
        {"reviewed_at": None},
    ],
)
def test_reviewed_version_requires_reviewer_and_timestamp(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(InvalidModelError):
        make_source_version(**overrides)


def test_reviewed_version_rejects_untrimmed_reviewer() -> None:
    with pytest.raises(InvalidModelError):
        make_source_version(reviewed_by=" local-corpus-owner")


@pytest.mark.parametrize(
    "review_metadata",
    [
        {"reviewed_by": "local-corpus-owner", "reviewed_at": REVIEWED_AT},
        {"reviewed_by": "local-corpus-owner"},
        {"reviewed_at": REVIEWED_AT},
    ],
)
def test_non_reviewed_version_cannot_have_review_metadata(
    review_metadata: dict[str, object],
) -> None:
    with pytest.raises(InvalidModelError):
        make_source_version(
            review_status=SourceReviewStatus.PENDING,
            **review_metadata,
        )


def test_rejects_version_that_names_itself_as_parent() -> None:
    with pytest.raises(InvalidModelError):
        make_source_version(parent_source_version_id=SOURCE_VERSION_ID)


def test_rejects_naive_acquisition_timestamp() -> None:
    with pytest.raises(InvalidModelError):
        make_source_version(acquired_at=datetime(2026, 7, 19, 12, 0))


def test_rejects_naive_review_timestamp() -> None:
    with pytest.raises(InvalidModelError):
        make_source_version(reviewed_at=datetime(2026, 7, 19, 13, 0))


def test_allows_pending_source_version_without_review_metadata() -> None:
    source_version = make_source_version(
        review_status=SourceReviewStatus.PENDING,
        reviewed_by=None,
        reviewed_at=None,
        parent_source_version_id=PARENT_SOURCE_VERSION_ID,
    )

    assert source_version.review_status is SourceReviewStatus.PENDING
    assert source_version.parent_source_version_id == PARENT_SOURCE_VERSION_ID


def test_allows_rejected_source_version_with_review_metadata() -> None:
    source_version = make_source_version(
        review_status=SourceReviewStatus.REJECTED,
    )

    assert source_version.review_status is SourceReviewStatus.REJECTED
    assert source_version.reviewed_by == "local-corpus-owner"
    assert source_version.reviewed_at == REVIEWED_AT


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": " Modern Family S01E01 reviewed subtitle"},
        {"origin": " private-local-corpus"},
    ],
)
def test_rejects_untrimmed_source_document_text(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(InvalidModelError):
        make_source_document(**overrides)
