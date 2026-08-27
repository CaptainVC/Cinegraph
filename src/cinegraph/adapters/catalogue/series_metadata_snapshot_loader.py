"""Strict, fail-closed loading and validation for published metadata snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from cinegraph.adapters.source.tvmaze_constants import (
    TVMAZE_ALLOWED_CONTENT_HOSTS,
    TVMAZE_ATTRIBUTION,
    TVMAZE_LICENSE_NAME,
    TVMAZE_LICENSE_URL,
    TVMAZE_PROVIDER_NAME,
)
from cinegraph.application.serialization.series_metadata_snapshot_serializer import (
    canonical_metadata_json,
)
from cinegraph.common.identifiers.generator import IdentifierGenerator
from cinegraph.domain.enums.enum import (
    RightsStatus,
    SourceAcquisitionMethod,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.series_metadata import (
    ArtworkAsset,
    CreditedPerson,
    CreditKind,
    EpisodeCastMetadata,
    SeriesMetadataSnapshot,
)
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef
from cinegraph.ports.dto.fetched_series_metadata import FetchedSeriesMetadata


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _CreditModel(_StrictModel):
    person_id: StrictInt = Field(gt=0)
    name: StrictStr
    canonical_url: StrictStr
    character_id: StrictInt | None = Field(default=None, gt=0)
    character: StrictStr
    character_url: StrictStr | None = None
    kind: StrictStr


class _PosterModel(_StrictModel):
    source_url: StrictStr
    canonical_url: StrictStr
    medium_url: StrictStr | None = None
    original_url: StrictStr | None = None
    provider_asset_id: StrictStr | None = None
    width: StrictInt | None = Field(default=None, gt=0)
    height: StrictInt | None = Field(default=None, gt=0)
    attribution: StrictStr
    license_name: StrictStr
    license_url: StrictStr
    retrieved_at: datetime


class _EpisodeModel(_StrictModel):
    series_id: UUID
    season_id: UUID
    episode_id: UUID
    season: StrictInt = Field(gt=0)
    episode: StrictInt = Field(gt=0)
    provider_episode_id: StrictInt = Field(gt=0)
    title: StrictStr
    canonical_url: StrictStr
    guest_cast: tuple[_CreditModel, ...]


class _MetadataModel(_StrictModel):
    series_id: UUID
    source_version_id: UUID
    provider: StrictStr
    provider_show_id: StrictInt = Field(gt=0)
    title: StrictStr
    canonical_url: StrictStr
    attribution: StrictStr
    license_name: StrictStr
    license_url: StrictStr
    poster: _PosterModel | None = None
    regular_cast: tuple[_CreditModel, ...]
    episodes: tuple[_EpisodeModel, ...] = Field(min_length=1)


class _SourceVersionModel(_StrictModel):
    source_document_id: UUID
    source_version_id: UUID
    content_hash: StrictStr
    rights_status: RightsStatus
    acquisition_method: SourceAcquisitionMethod
    review_status: SourceReviewStatus
    status: SourceVersionStatus
    acquired_at: datetime
    parent_source_version_id: UUID | None = None
    reviewed_by: StrictStr | None = None
    reviewed_at: datetime | None = None


class _SnapshotFileModel(_StrictModel):
    source_version: _SourceVersionModel
    metadata: _MetadataModel


def _invalid(detail: str = "Series metadata snapshot is invalid.") -> InvalidModelError:
    return InvalidModelError(detail)


def _url(value: str, *, canonical: bool = False) -> str:
    parsed = urlparse(value)
    if (
        not value
        or value.strip() != value
        or parsed.scheme != "https"
        or parsed.hostname not in TVMAZE_ALLOWED_CONTENT_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise _invalid("Series metadata contains an untrusted URL.")
    if canonical and parsed.path == "/":
        raise _invalid("Series metadata canonical URL is invalid.")
    return value


def _trimmed(value: str) -> str:
    if not value or value.strip() != value:
        raise _invalid("Series metadata contains an empty or untrimmed value.")
    return value


def _credit(model: _CreditModel, expected_kind: str) -> CreditedPerson:
    if model.kind != expected_kind:
        raise _invalid("Series metadata credit kind does not match its scope.")
    return CreditedPerson(
        provider_person_id=model.person_id,
        name=_trimmed(model.name),
        canonical_url=_url(model.canonical_url, canonical=True),
        character_name=_trimmed(model.character),
        character_provider_id=model.character_id,
        character_canonical_url=(
            _url(model.character_url, canonical=True)
            if model.character_url is not None
            else None
        ),
        credit_kind=CreditKind(expected_kind),
    )


def _poster(model: _PosterModel | None) -> ArtworkAsset | None:
    if model is None:
        return None
    if (
        model.attribution != TVMAZE_ATTRIBUTION
        or model.license_name != TVMAZE_LICENSE_NAME
        or model.license_url != TVMAZE_LICENSE_URL
    ):
        raise _invalid("Series metadata poster provenance is not approved.")
    return ArtworkAsset(
        source_url=_url(model.source_url),
        canonical_url=_url(model.canonical_url, canonical=True),
        medium_url=_url(model.medium_url) if model.medium_url else None,
        original_url=_url(model.original_url) if model.original_url else None,
        provider_asset_id=_trimmed(model.provider_asset_id)
        if model.provider_asset_id
        else None,
        width=model.width,
        height=model.height,
        attribution=_trimmed(model.attribution),
        license_name=_trimmed(model.license_name),
        license_url=_license_url(model.license_url),
        retrieved_at=model.retrieved_at,
    )


def _license_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        value != TVMAZE_LICENSE_URL
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise _invalid("Series metadata license URL is not approved.")
    return value


def _snapshot_models(
    source: _SnapshotFileModel,
    catalogue: CatalogueManifest,
    *,
    approved_only: bool,
) -> tuple[SourceVersion, SeriesMetadataSnapshot]:
    version = source.source_version
    metadata = source.metadata
    if any(
        identifier.int == 0
        for identifier in (
            version.source_document_id,
            version.source_version_id,
            metadata.series_id,
            metadata.source_version_id,
        )
    ) or (
        version.parent_source_version_id is not None
        and version.parent_source_version_id.int == 0
    ):
        raise _invalid("Series metadata contains a zero UUID.")
    series = next(
        (item for item in catalogue.series if item.series_id == metadata.series_id), None
    )
    if series is None or metadata.title != series.series_name:
        raise _invalid("Series metadata does not exactly match the catalogue series.")
    if metadata.provider != TVMAZE_PROVIDER_NAME:
        raise _invalid("Series metadata provider is not the approved TVmaze provider.")
    if metadata.attribution != TVMAZE_ATTRIBUTION:
        raise _invalid("Series metadata attribution is not the approved TVmaze attribution.")
    if metadata.license_name != TVMAZE_LICENSE_NAME or metadata.license_url != TVMAZE_LICENSE_URL:
        raise _invalid("Series metadata license is not the approved TVmaze license.")
    _url(metadata.canonical_url, canonical=True)
    if version.rights_status is not RightsStatus.ALLOWED:
        raise _invalid("Only metadata with ALLOWED rights can be published.")
    if version.acquisition_method is not SourceAcquisitionMethod.TVMAZE_API:
        raise _invalid("Only TVmaze-acquired metadata can be published.")
    if version.status is not SourceVersionStatus.ACTIVE:
        raise _invalid("Only active metadata versions can be published.")
    if approved_only and version.review_status in {
        SourceReviewStatus.PENDING,
        SourceReviewStatus.REJECTED,
    }:
        raise _invalid("Pending or rejected metadata cannot be published.")
    expected_document = IdentifierGenerator.series_metadata_source_document_id(
        metadata.series_id, "tvmaze"
    )
    if version.source_document_id != expected_document:
        raise _invalid("Series metadata source document ID is invalid.")
    if version.source_version_id != metadata.source_version_id:
        raise _invalid("Series metadata source version IDs do not match.")

    poster = _poster(metadata.poster)
    regular_cast = tuple(_credit(item, "regular") for item in metadata.regular_cast)
    if len(
        {
            (credit.provider_person_id, credit.character_provider_id, credit.character_name)
            for credit in regular_cast
        }
    ) != len(regular_cast):
        raise _invalid("Series metadata regular cast must be unique.")
    catalogue_episodes = {
        episode.episode_id: (season.season_id, season.season_number, episode)
        for season in series.seasons
        for episode in season.episodes
    }
    episode_items: list[EpisodeCastMetadata] = []
    seen_ids: set[UUID] = set()
    seen_positions: set[tuple[int, int]] = set()
    for item in metadata.episodes:
        if item.episode_id in seen_ids or (item.season, item.episode) in seen_positions:
            raise _invalid("Series metadata episodes must be unique.")
        seen_ids.add(item.episode_id)
        seen_positions.add((item.season, item.episode))
        mapped = catalogue_episodes.get(item.episode_id)
        if mapped is None:
            raise _invalid("Series metadata episode is not in the catalogue.")
        season_id, season_number, catalogue_episode = mapped
        if (
            item.series_id != series.series_id
            or item.season_id != season_id
            or item.season != season_number
            or item.episode != catalogue_episode.episode_number
            or item.title.casefold() != catalogue_episode.episode_title.casefold()
        ):
            raise _invalid("Series metadata episode does not exactly match the catalogue.")
        guest_cast = tuple(_credit(credit, "guest") for credit in item.guest_cast)
        if len(
            {
                (credit.provider_person_id, credit.character_provider_id, credit.character_name)
                for credit in guest_cast
            }
        ) != len(guest_cast):
            raise _invalid("Series metadata guest cast must be unique per episode.")
        episode_items.append(
            EpisodeCastMetadata(
                episode=EpisodeRef(
                    series.series_id,
                    season_id,
                    item.episode_id,
                    EpisodePosition(item.season, item.episode),
                ),
                provider_episode_id=item.provider_episode_id,
                title=_trimmed(item.title),
                canonical_url=_url(item.canonical_url, canonical=True),
                guest_cast=guest_cast,
            )
        )
    if seen_ids != set(catalogue_episodes):
        raise _invalid("Series metadata must cover exactly the catalogue episodes.")
    positions = tuple((item.season_number, item.episode_number) for item in episode_items)
    if positions != tuple(sorted(positions)):
        raise _invalid("Series metadata episodes must be sorted by catalogue position.")

    snapshot = SeriesMetadataSnapshot(
        series_id=metadata.series_id,
        source_version_id=metadata.source_version_id,
        provider_name=metadata.provider,
        provider_show_id=metadata.provider_show_id,
        title=_trimmed(metadata.title),
        canonical_url=_url(metadata.canonical_url, canonical=True),
        poster=poster,
        regular_cast=regular_cast,
        episodes=tuple(episode_items),
        rights_status=version.rights_status,
        attribution=metadata.attribution,
        license_name=metadata.license_name,
        license_url=_license_url(metadata.license_url),
    )
    fetched = FetchedSeriesMetadata(
        provider_name=snapshot.provider_name,
        provider_show_id=snapshot.provider_show_id,
        title=snapshot.title,
        canonical_url=snapshot.canonical_url,
        poster=snapshot.poster,
        regular_cast=snapshot.regular_cast,
        episodes=snapshot.episodes,
        retrieved_at=version.acquired_at,
        attribution=snapshot.attribution,
        license_name=snapshot.license_name,
        license_url=snapshot.license_url,
    )
    expected_hash = hashlib.sha256(
        canonical_metadata_json(fetched).encode("utf-8")
    ).hexdigest()
    if version.content_hash != expected_hash:
        raise _invalid("Series metadata content hash does not match canonical metadata.")
    expected_version_id = IdentifierGenerator.source_version_id(
        version.source_document_id, version.content_hash
    )
    if version.source_version_id != expected_version_id:
        raise _invalid("Series metadata source version ID is invalid.")
    try:
        source_version = SourceVersion(
            source_version_id=version.source_version_id,
            source_document_id=version.source_document_id,
            content_hash=version.content_hash,
            rights_status=version.rights_status,
            acquisition_method=version.acquisition_method,
            review_status=version.review_status,
            status=version.status,
            acquired_at=version.acquired_at,
            parent_source_version_id=version.parent_source_version_id,
            reviewed_by=version.reviewed_by,
            reviewed_at=version.reviewed_at,
        )
    except InvalidModelError as error:
        raise _invalid(str(error)) from error
    return source_version, snapshot


def parse_series_metadata_snapshot(
    path: Path,
    catalogue: CatalogueManifest,
    *,
    approved_only: bool = True,
) -> tuple[SourceVersion, SeriesMetadataSnapshot]:
    """Parse and validate one snapshot without returning raw provider payloads."""
    if not isinstance(path, Path) or not path.is_file():
        raise _invalid("Series metadata snapshot path must identify a readable file.")
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = _SnapshotFileModel.model_validate_json(raw)
        version, snapshot = _snapshot_models(
            parsed, catalogue, approved_only=approved_only
        )
    except (OSError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
        if isinstance(error, InvalidModelError):
            raise
        raise _invalid() from error
    return version, snapshot


class JsonSeriesMetadataSnapshotLoader:
    """Load all approved snapshots, failing closed on any invalid JSON artifact."""

    def load_directory(
        self, path: Path, catalogue: CatalogueManifest
    ) -> Mapping[UUID, SeriesMetadataSnapshot]:
        if not path.exists():
            return MappingProxyType({})
        if not path.is_dir():
            raise _invalid("Approved series metadata path must be a directory.")
        loaded: dict[UUID, SeriesMetadataSnapshot] = {}
        for item in sorted(path.iterdir(), key=lambda candidate: candidate.name):
            if not item.is_file():
                continue
            if item.suffix.lower() != ".json":
                raise _invalid("Approved series metadata directory contains an unknown file.")
            _, snapshot = parse_series_metadata_snapshot(item, catalogue)
            if snapshot.series_id in loaded:
                raise _invalid("Approved series metadata contains duplicate series IDs.")
            loaded[snapshot.series_id] = snapshot
        return MappingProxyType(loaded)
