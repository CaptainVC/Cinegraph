import hashlib
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from cinegraph.application.models.ingest_reviewed_corpus import (
    ReviewedSubtitleBatch,
    ReviewedSubtitleBatchItem,
)
from cinegraph.common.error_messages import CorpusIngestionErrorMessages
from cinegraph.domain.enums.enum import SourceReviewStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest


class _LedgerModelBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _LedgerRecord(_LedgerModelBase):
    candidate_filename: StrictStr
    reviewed_filename: StrictStr
    candidate_sha256: StrictStr
    reviewed_sha256: StrictStr
    promoted_question_mark_labels: StrictInt = Field(ge=0)
    removed_redaction_lines: StrictInt = Field(ge=0)
    removed_cue_numbers: tuple[StrictInt, ...]


class _ReviewLedger(_LedgerModelBase):
    schema_version: StrictInt
    review_status: SourceReviewStatus
    reviewed_by: StrictStr
    reviewed_at: datetime
    records: tuple[_LedgerRecord, ...] = Field(min_length=1)


class ReviewedSubtitleLedgerLoader:
    # Verify review provenance, file hashes, and explicit catalogue mappings.
    def load(
        self,
        manifest: CatalogueManifest,
        review_ledger_path: Path,
        reviewed_directory: Path,
    ) -> ReviewedSubtitleBatch:
        try:
            ledger = _ReviewLedger.model_validate_json(
                review_ledger_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as error:
            raise InvalidModelError(
                CorpusIngestionErrorMessages.REVIEW_LEDGER_MUST_BE_VALID
            ) from error
        if (
            ledger.schema_version != 1
            or ledger.review_status is not SourceReviewStatus.REVIEWED
            or not ledger.reviewed_by
            or ledger.reviewed_by.strip() != ledger.reviewed_by
        ):
            raise InvalidModelError(
                CorpusIngestionErrorMessages.REVIEW_LEDGER_MUST_BE_VALID
            )
        filenames = tuple(item.reviewed_filename for item in ledger.records)
        if len(filenames) != len(set(filenames)):
            raise InvalidModelError(
                CorpusIngestionErrorMessages.REVIEW_LEDGER_RECORD_FILENAMES_MUST_BE_UNIQUE
            )

        episodes = tuple(
            (series, season, episode)
            for series in manifest.series
            for season in series.seasons
            for episode in season.episodes
            if episode.reviewed_subtitle_filename is not None
        )
        catalogue_filenames = tuple(
            episode.reviewed_subtitle_filename for _, _, episode in episodes
        )
        if len(catalogue_filenames) != len(set(catalogue_filenames)):
            raise InvalidModelError(
                CorpusIngestionErrorMessages.CATALOGUE_SUBTITLE_FILENAMES_MUST_BE_UNIQUE
            )
        episode_by_filename = {
            episode.reviewed_subtitle_filename: (series, season, episode)
            for series, season, episode in episodes
        }

        items = []
        refs = {ref.episode_id: ref for ref in manifest.episode_refs()}
        for record in ledger.records:
            mapped = episode_by_filename.get(record.reviewed_filename)
            if mapped is None:
                raise InvalidModelError(
                    CorpusIngestionErrorMessages.REVIEWED_SUBTITLE_MUST_MAP_TO_CATALOGUE
                )
            series, _, episode = mapped
            source_path = reviewed_directory / record.reviewed_filename
            if not source_path.is_file():
                raise InvalidModelError(
                    CorpusIngestionErrorMessages.REVIEWED_SUBTITLE_FILE_MUST_EXIST
                )
            content_hash = hashlib.sha256(
                source_path.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest()
            if content_hash != record.reviewed_sha256:
                raise InvalidModelError(
                    CorpusIngestionErrorMessages.REVIEWED_SUBTITLE_HASH_MUST_MATCH_LEDGER
                )
            items.append(
                ReviewedSubtitleBatchItem(
                    episode=refs[episode.episode_id],
                    episode_title=f"{series.series_name}: {episode.episode_title}",
                    source_path=source_path,
                    content_sha256=content_hash,
                    reviewed_by=ledger.reviewed_by,
                    reviewed_at=ledger.reviewed_at,
                    review_status=ledger.review_status,
                )
            )
        return ReviewedSubtitleBatch(items=tuple(items))
