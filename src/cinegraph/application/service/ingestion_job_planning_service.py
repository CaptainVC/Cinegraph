from cinegraph.application.models.ingestion_job import EnqueueIngestionJob, IngestionInventoryReport
from cinegraph.application.service.ingestion_job_service import IngestionJobService
from cinegraph.domain.enums.enum import CorpusReadinessStatus, IngestionJobKind
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.ingestion_job import IngestionJob


class IngestionJobPlanningService:
    """Turn safe inventory statuses into idempotent jobs; never executes a job."""

    def __init__(self, job_service: IngestionJobService) -> None:
        self._job_service = job_service

    def plan(
        self,
        manifest: CatalogueManifest,
        report: IngestionInventoryReport,
        pipeline_revision: str,
        enqueue: bool = False,
    ) -> tuple[EnqueueIngestionJob | IngestionJob, ...]:
        series_by_episode = {
            episode.episode_id: series.series_id
            for series in manifest.series
            for season in series.seasons
            for episode in season.episodes
        }
        plans: list[EnqueueIngestionJob | IngestionJob] = []
        kinds = {
            CorpusReadinessStatus.REVIEWED_READY: IngestionJobKind.TRANSCRIPT_INGESTION,
            CorpusReadinessStatus.AWAITING_AUTOMATED_REVIEW: IngestionJobKind.SPEAKER_REVIEW,
            CorpusReadinessStatus.AWAITING_ALIGNMENT: IngestionJobKind.SUBTITLE_ALIGNMENT,
        }
        for item in report.items:
            kind = kinds.get(item.status)
            if kind is None or item.content_sha256 is None:
                continue
            command = EnqueueIngestionJob(
                kind=kind,
                series_id=series_by_episode[item.episode_id],
                season_number=item.season_number,
                episode_number=item.episode_number,
                source_fingerprint=item.content_sha256,
                pipeline_revision=pipeline_revision,
            )
            plans.append(self._job_service.enqueue(command) if enqueue else command)
        return tuple(plans)
