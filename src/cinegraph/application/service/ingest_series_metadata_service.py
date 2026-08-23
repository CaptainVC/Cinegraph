from hashlib import sha256

from cinegraph.application.serialization.series_metadata_snapshot_serializer import (
    canonical_metadata_json,
)
from cinegraph.common.error_messages.source import SourceErrorMessages
from cinegraph.common.identifiers.generator import IdentifierGenerator
from cinegraph.domain.enums.enum import (
    RightsStatus,
    SourceAcquisitionMethod,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.series_metadata import SeriesMetadataSnapshot
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.ingestion.series_metadata.ingest_series_metadata import (
    IngestSeriesMetadataCommand,
    IngestSeriesMetadataResult,
)
from cinegraph.ports.dto.fetched_series_metadata import FetchedSeriesMetadata
from cinegraph.ports.repository.series_metadata_ingestion_repository import (
    SeriesMetadataIngestionRepository,
)
from cinegraph.ports.series_metadata.series_metadata_provider import (
    SeriesMetadataProvider,
)


class IngestSeriesMetadataService:
    def __init__(
        self,
        provider: SeriesMetadataProvider,
        repository: SeriesMetadataIngestionRepository,
    ) -> None:
        self._provider = provider
        self._repository = repository

    @staticmethod
    def _validate_fetched(
        command: IngestSeriesMetadataCommand,
        fetched: FetchedSeriesMetadata,
    ) -> None:
        if (
            not isinstance(fetched.provider_show_id, int)
            or isinstance(fetched.provider_show_id, bool)
            or fetched.provider_show_id != command.provider_show_id
        ):
            raise InvalidModelError(SourceErrorMessages.TVMAZE_SHOW_ID_MISMATCH)
        if (
            not isinstance(fetched.title, str)
            or fetched.title.casefold() != command.expected_title.casefold()
        ):
            raise InvalidModelError(SourceErrorMessages.METADATA_SHOW_TITLE_MISMATCH)
        if command.rights_status is not RightsStatus.ALLOWED:
            raise InvalidModelError(
                SourceErrorMessages.SERIES_METADATA_RIGHTS_MUST_BE_ALLOWED
            )
        requested = {
            (item.position.season_number, item.position.episode_number)
            for item in command.episodes
        }
        received = {
            (item.season_number, item.episode_number) for item in fetched.episodes
        }
        if (
            len(fetched.episodes) != len(requested)
            or received != requested
            or any(
                item.episode.series_id != command.series_id for item in fetched.episodes
            )
        ):
            raise InvalidModelError(SourceErrorMessages.TVMAZE_EPISODE_NOT_FOUND)

    def execute(
        self,
        command: IngestSeriesMetadataCommand,
    ) -> IngestSeriesMetadataResult:
        fetched = self._provider.fetch(
            provider_show_id=command.provider_show_id,
            expected_title=command.expected_title,
            series_id=command.series_id,
            episodes=command.episodes,
        )
        self._validate_fetched(command, fetched)
        content_hash = sha256(
            canonical_metadata_json(fetched).encode("utf-8")
        ).hexdigest()
        existing = self._repository.find_active_version_by_content_hash(
            command.source_document.source_document_id,
            content_hash,
        )
        if existing is not None:
            return IngestSeriesMetadataResult(existing, None, True)

        previous = self._repository.get_active_version(
            command.source_document.source_document_id
        )
        source_version = SourceVersion(
            source_version_id=IdentifierGenerator.source_version_id(
                command.source_document.source_document_id,
                content_hash,
            ),
            source_document_id=command.source_document.source_document_id,
            content_hash=content_hash,
            rights_status=RightsStatus.ALLOWED,
            acquisition_method=SourceAcquisitionMethod.TVMAZE_API,
            review_status=SourceReviewStatus.PENDING,
            status=SourceVersionStatus.ACTIVE,
            acquired_at=fetched.retrieved_at,
            parent_source_version_id=(
                previous.source_version_id if previous is not None else None
            ),
        )
        snapshot = SeriesMetadataSnapshot(
            series_id=command.series_id,
            source_version_id=source_version.source_version_id,
            provider_name=fetched.provider_name,
            provider_show_id=fetched.provider_show_id,
            title=fetched.title,
            canonical_url=fetched.canonical_url,
            poster=fetched.poster,
            regular_cast=fetched.regular_cast,
            episodes=fetched.episodes,
            rights_status=RightsStatus.ALLOWED,
            attribution=fetched.attribution,
            license_name=fetched.license_name,
            license_url=fetched.license_url,
        )
        self._repository.persist_new_series_metadata_ingestion(
            command.source_document,
            source_version,
            previous,
            snapshot,
        )
        return IngestSeriesMetadataResult(source_version, snapshot, False)
