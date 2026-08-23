from __future__ import annotations

import json

from cinegraph.domain.models.series_metadata import (
    CreditedPerson,
    SeriesMetadataSnapshot,
)
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.ingestion.series_metadata.ingest_series_metadata import (
    IngestSeriesMetadataResult,
)
from cinegraph.ports.dto.fetched_series_metadata import FetchedSeriesMetadata


def _credit_payload(credit: CreditedPerson) -> dict[str, object]:
    return {
        "person_id": credit.provider_person_id,
        "name": credit.name,
        "canonical_url": credit.canonical_url,
        "character_id": credit.character_provider_id,
        "character": credit.character_name,
        "character_url": credit.character_canonical_url,
        "kind": credit.credit_kind.value,
    }


def canonical_metadata_payload(fetched: FetchedSeriesMetadata) -> dict[str, object]:
    poster = None
    if fetched.poster is not None:
        poster = {
            "source_url": fetched.poster.source_url,
            "canonical_url": fetched.poster.canonical_url,
            "medium_url": fetched.poster.medium_url,
            "original_url": fetched.poster.original_url,
            "provider_asset_id": fetched.poster.provider_asset_id,
            "width": fetched.poster.width,
            "height": fetched.poster.height,
            "attribution": fetched.poster.attribution,
            "license_name": fetched.poster.license_name,
            "license_url": fetched.poster.license_url,
        }
    return {
        "provider": fetched.provider_name,
        "provider_show_id": fetched.provider_show_id,
        "title": fetched.title,
        "canonical_url": fetched.canonical_url,
        "poster": poster,
        "regular_cast": [_credit_payload(item) for item in fetched.regular_cast],
        "episodes": [
            {
                "series_id": str(item.episode.series_id),
                "season_id": str(item.episode.season_id),
                "episode_id": str(item.episode.episode_id),
                "season": item.season_number,
                "episode": item.episode_number,
                "provider_episode_id": item.provider_episode_id,
                "title": item.title,
                "canonical_url": item.canonical_url,
                "guest_cast": [_credit_payload(credit) for credit in item.guest_cast],
            }
            for item in fetched.episodes
        ],
        "attribution": fetched.attribution,
        "license_name": fetched.license_name,
        "license_url": fetched.license_url,
    }


def canonical_metadata_json(fetched: FetchedSeriesMetadata) -> str:
    return json.dumps(
        canonical_metadata_payload(fetched),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def export_ingestion_result(
    source_document: SourceDocument,
    result: IngestSeriesMetadataResult,
) -> dict[str, object]:
    snapshot = result.snapshot
    if snapshot is None:
        raise ValueError("An already-ingested result has no snapshot to export.")
    return {
        "source_version": {
            "source_document_id": str(source_document.source_document_id),
            "source_version_id": str(result.source_version.source_version_id),
            "content_hash": result.source_version.content_hash,
            "rights_status": result.source_version.rights_status.value,
            "acquisition_method": result.source_version.acquisition_method.value,
            "review_status": result.source_version.review_status.value,
            "status": result.source_version.status.value,
            "acquired_at": result.source_version.acquired_at.isoformat(),
            "parent_source_version_id": (
                str(result.source_version.parent_source_version_id)
                if result.source_version.parent_source_version_id
                else None
            ),
        },
        "metadata": snapshot_payload(snapshot),
    }


def snapshot_payload(snapshot: SeriesMetadataSnapshot) -> dict[str, object]:
    poster = None
    if snapshot.poster is not None:
        poster = {
            "source_url": snapshot.poster.source_url,
            "canonical_url": snapshot.poster.canonical_url,
            "medium_url": snapshot.poster.medium_url,
            "original_url": snapshot.poster.original_url,
            "provider_asset_id": snapshot.poster.provider_asset_id,
            "width": snapshot.poster.width,
            "height": snapshot.poster.height,
            "attribution": snapshot.poster.attribution,
            "license_name": snapshot.poster.license_name,
            "license_url": snapshot.poster.license_url,
            "retrieved_at": snapshot.poster.retrieved_at.isoformat(),
        }
    return {
        "series_id": str(snapshot.series_id),
        "source_version_id": str(snapshot.source_version_id),
        "provider": snapshot.provider_name,
        "provider_show_id": snapshot.provider_show_id,
        "title": snapshot.title,
        "canonical_url": snapshot.canonical_url,
        "attribution": snapshot.attribution,
        "license_name": snapshot.license_name,
        "license_url": snapshot.license_url,
        "poster": poster,
        "regular_cast": [_credit_payload(item) for item in snapshot.regular_cast],
        "episodes": [
            {
                "series_id": str(item.episode.series_id),
                "season_id": str(item.episode.season_id),
                "episode_id": str(item.episode.episode_id),
                "season": item.season_number,
                "episode": item.episode_number,
                "provider_episode_id": item.provider_episode_id,
                "title": item.title,
                "canonical_url": item.canonical_url,
                "guest_cast": [_credit_payload(credit) for credit in item.guest_cast],
            }
            for item in snapshot.episodes
        ],
    }


def export_json(
    source_document: SourceDocument, result: IngestSeriesMetadataResult
) -> str:
    return (
        json.dumps(
            export_ingestion_result(source_document, result),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
