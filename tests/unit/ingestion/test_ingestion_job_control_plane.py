import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.adapters.persistence.database import create_database_engine
from cinegraph.adapters.persistence.migration_runner import upgrade_database
from cinegraph.adapters.persistence.sqlalchemy_ingestion_job_repository import (
    IngestionJobRow,
    SqlAlchemyIngestionJobUnitOfWorkFactory,
)
from cinegraph.adapters.repository.in_memory.in_memory_ingestion_job_repository import (
    InMemoryIngestionJobUnitOfWorkFactory,
)
from cinegraph.application.models.ingestion_job import EnqueueIngestionJob
from cinegraph.application.service.corpus_inventory_service import CorpusInventoryService
from cinegraph.application.service.ingestion_job_service import IngestionJobService
from cinegraph.config import (
    DEFAULT_INGESTION_JOB_CONFIGURATION,
    CinegraphRuntimeSettings,
    IngestionJobConfiguration,
)
from cinegraph.domain.enums.enum import (
    CorpusInventoryReason,
    CorpusReadinessStatus,
    IngestionJobEventKind,
    IngestionJobKind,
    IngestionJobStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.catalogue.episode import Episode
from cinegraph.domain.models.catalogue.season import Season
from cinegraph.domain.models.catalogue.series import Series
from cinegraph.domain.models.ingestion_job import IngestionJobEvent


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def now_utc(self) -> datetime:
        return self.value


def _command(
    *, scheduled_at: datetime | None = None, max_attempts: int | None = None
) -> EnqueueIngestionJob:
    return EnqueueIngestionJob(
        kind=IngestionJobKind.SPEAKER_REVIEW,
        series_id=UUID(int=11),
        season_number=2,
        episode_number=1,
        source_fingerprint="a" * 64,
        pipeline_revision="phase27-v1",
        scheduled_at=scheduled_at,
        max_attempts=max_attempts,
    )


def test_in_memory_lifecycle_is_idempotent_and_append_only() -> None:
    clock = _Clock()
    service = IngestionJobService(InMemoryIngestionJobUnitOfWorkFactory(), clock)
    job = service.enqueue(_command())
    assert service.enqueue(_command()).job_id == job.job_id
    claimed = service.claim_next("worker-a")
    assert claimed is not None
    assert claimed.status is IngestionJobStatus.RUNNING
    with pytest.raises(InvalidModelError):
        service.succeed(job.job_id, "worker-b")
    assert service.heartbeat(job.job_id, "worker-a").lease_owner == "worker-a"
    assert service.succeed(job.job_id, "worker-a").status is IngestionJobStatus.SUCCEEDED
    assert tuple(event.sequence_number for event in service.events(job.job_id)) == (1, 2, 3, 4)


def test_schedule_and_retry_then_terminal_failure() -> None:
    clock = _Clock()
    service = IngestionJobService(InMemoryIngestionJobUnitOfWorkFactory(), clock)
    job = service.enqueue(_command(scheduled_at=clock.value + timedelta(hours=1), max_attempts=2))
    assert service.claim_next("worker-a") is None
    clock.value += timedelta(hours=1)
    assert service.claim_next("worker-a") is not None
    retried = service.fail_or_retry(job.job_id, "worker-a", "speaker_review_failed")
    assert retried.status is IngestionJobStatus.PENDING
    assert retried.next_attempt_at is not None
    clock.value = retried.next_attempt_at + timedelta(seconds=1)
    assert service.claim_next("worker-a") is not None
    failed = service.fail_or_retry(job.job_id, "worker-a", "speaker_review_failed")
    assert failed.status is IngestionJobStatus.FAILED


def test_sqlite_migration_and_restart_preserve_enqueued_job(tmp_path: Path) -> None:
    settings = CinegraphRuntimeSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'jobs.sqlite3'}"
    )
    upgrade_database(settings)
    engine = create_database_engine(settings)
    try:
        first = IngestionJobService(SqlAlchemyIngestionJobUnitOfWorkFactory(engine), SystemClock())
        job = first.enqueue(_command())
        second = IngestionJobService(SqlAlchemyIngestionJobUnitOfWorkFactory(engine), SystemClock())
        assert second.enqueue(_command()).job_id == job.job_id
        assert second.claim_next("worker-a") is not None
    finally:
        engine.dispose()


def test_sqlalchemy_unit_of_work_rejects_reentry(tmp_path: Path) -> None:
    settings = CinegraphRuntimeSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'reentry.sqlite3'}"
    )
    upgrade_database(settings)
    engine = create_database_engine(settings)
    try:
        unit_of_work = SqlAlchemyIngestionJobUnitOfWorkFactory(engine)()
        with unit_of_work:
            with pytest.raises(RuntimeError):
                unit_of_work.__enter__()
    finally:
        engine.dispose()


def test_sqlite_rejects_terminal_scope_metadata_without_started_at(tmp_path: Path) -> None:
    settings = CinegraphRuntimeSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'coherence.sqlite3'}"
    )
    upgrade_database(settings)
    engine = create_database_engine(settings)
    try:
        service = IngestionJobService(SqlAlchemyIngestionJobUnitOfWorkFactory(engine), _Clock())
        job = service.enqueue(_command())
        with SqlAlchemyIngestionJobUnitOfWorkFactory(engine)() as unit_of_work:
            with pytest.raises(IntegrityError):
                unit_of_work._session.execute(
                    update(IngestionJobRow)
                    .where(IngestionJobRow.job_id == job.job_id)
                    .values(
                        status=IngestionJobStatus.SUCCEEDED.value,
                        attempts=1,
                        finished_at=datetime(2026, 1, 1, tzinfo=UTC),
                    )
                )
    finally:
        engine.dispose()


def test_sqlite_duplicate_idempotency_returns_existing_winner(tmp_path: Path) -> None:
    settings = CinegraphRuntimeSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'duplicate.sqlite3'}"
    )
    upgrade_database(settings)
    engine = create_database_engine(settings)
    try:
        job = IngestionJobService(InMemoryIngestionJobUnitOfWorkFactory(), _Clock()).enqueue(
            _command()
        )
        duplicate = replace(job, job_id=UUID(int=9876))
        factory = SqlAlchemyIngestionJobUnitOfWorkFactory(engine)
        with factory() as unit_of_work:
            unit_of_work.jobs.add(job)
            winner = unit_of_work.jobs.add(duplicate)
            assert winner.job_id == job.job_id
            unit_of_work.rollback()
    finally:
        engine.dispose()


def test_sqlalchemy_owned_save_rejects_stale_same_worker_lease(tmp_path: Path) -> None:
    settings = CinegraphRuntimeSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'stale.sqlite3'}"
    )
    upgrade_database(settings)
    engine = create_database_engine(settings)
    clock = _Clock()
    try:
        factory = SqlAlchemyIngestionJobUnitOfWorkFactory(engine)
        configuration = IngestionJobConfiguration(
            lease_duration=timedelta(minutes=1),
            heartbeat_extension=timedelta(minutes=10),
            retry_base_delay=timedelta(seconds=1),
            retry_max_delay=timedelta(seconds=2),
            default_max_attempts=2,
            claim_batch_size=1,
        )
        service = IngestionJobService(factory, clock, configuration)
        job = service.enqueue(_command())
        claimed = service.claim_next("worker-a")
        assert claimed is not None and claimed.lease_expires_at is not None
        service.heartbeat(job.job_id, "worker-a")
        stale = claimed.heartbeat("worker-a", clock.value, clock.value + timedelta(minutes=5))
        with factory() as unit_of_work:
            with pytest.raises(ValueError):
                unit_of_work.jobs.save_owned(
                    stale,
                    "worker-a",
                    clock.value,
                    claimed.lease_expires_at,
                )
    finally:
        engine.dispose()


def test_sqlite_reclaim_records_error_coherent_event(tmp_path: Path) -> None:
    settings = CinegraphRuntimeSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'reclaim.sqlite3'}"
    )
    upgrade_database(settings)
    engine = create_database_engine(settings)
    clock = _Clock()
    try:
        configuration = IngestionJobConfiguration(
            lease_duration=timedelta(seconds=1),
            heartbeat_extension=timedelta(seconds=1),
            retry_base_delay=timedelta(seconds=1),
            retry_max_delay=timedelta(seconds=2),
            default_max_attempts=2,
            claim_batch_size=1,
        )
        service = IngestionJobService(
            SqlAlchemyIngestionJobUnitOfWorkFactory(engine), clock, configuration
        )
        job = service.enqueue(_command(max_attempts=2))
        assert service.claim_next("worker-a") is not None
        clock.value += timedelta(seconds=2)
        reclaimed = service.reclaim_expired(job.job_id)
        assert reclaimed.status is IngestionJobStatus.PENDING
        event = service.events(job.job_id)[-1]
        assert event.kind is IngestionJobEventKind.RECLAIMED
        assert event.worker_id is None
        assert event.error_code == "lease_expired"
    finally:
        engine.dispose()


def test_job_domain_rejects_untrusted_shape_and_lifecycle_values() -> None:
    clock = _Clock()
    service = IngestionJobService(InMemoryIngestionJobUnitOfWorkFactory(), clock)
    base = service.enqueue(_command())
    invalid = (
        {"idempotency_key": "x"},
        {"source_fingerprint": "x" * 64},
        {"pipeline_revision": "bad revision"},
        {"season_number": 0},
        {"episode_number": 0},
        {"priority": 101},
        {"max_attempts": 21},
        {"attempts": 4, "max_attempts": 3},
        {"created_at": datetime(2026, 1, 1)},
        {"status": IngestionJobStatus.RUNNING},
        {"status": IngestionJobStatus.SUCCEEDED},
        {"status": IngestionJobStatus.PENDING, "finished_at": clock.value},
        {
            "status": IngestionJobStatus.RUNNING,
            "finished_at": clock.value,
            "lease_owner": "worker-a",
            "lease_expires_at": clock.value + timedelta(minutes=1),
        },
        {
            "status": IngestionJobStatus.SUCCEEDED,
            "finished_at": clock.value,
            "last_error_code": "source_invalid",
        },
        {"status": IngestionJobStatus.SUCCEEDED, "attempts": 1, "finished_at": clock.value},
        {
            "status": IngestionJobStatus.SUCCEEDED,
            "attempts": 1,
            "started_at": clock.value,
            "finished_at": clock.value,
            "next_attempt_at": clock.value + timedelta(minutes=1),
        },
        {"last_error_code": "not_allowlisted"},
    )
    for changes in invalid:
        with pytest.raises(InvalidModelError):
            replace(base, **changes)
    with pytest.raises(InvalidModelError):
        replace(base, episode_number=1, season_number=None)


@pytest.mark.parametrize(
    "changes",
    (
        {"lease_duration": timedelta(0)},
        {"heartbeat_extension": timedelta(seconds=-1)},
        {"retry_base_delay": timedelta(seconds=2), "retry_max_delay": timedelta(seconds=1)},
        {"default_max_attempts": 21},
        {"claim_batch_size": 0},
    ),
)
def test_ingestion_job_configuration_rejects_invalid_bounds(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        replace(DEFAULT_INGESTION_JOB_CONFIGURATION, **changes)


def test_job_domain_rejects_invalid_transitions_and_reclaims() -> None:
    clock = _Clock()
    service = IngestionJobService(InMemoryIngestionJobUnitOfWorkFactory(), clock)
    job = service.enqueue(_command(max_attempts=1))
    with pytest.raises(InvalidModelError):
        job.claim("bad worker!", clock.value, clock.value + timedelta(minutes=1))
    with pytest.raises(InvalidModelError):
        job.claim("worker-a", clock.value, clock.value)
    running = job.claim("worker-a", clock.value, clock.value + timedelta(minutes=1))
    with pytest.raises(InvalidModelError):
        running.heartbeat("worker-a", clock.value, running.lease_expires_at)
    with pytest.raises(InvalidModelError):
        running.heartbeat("worker-a", clock.value, clock.value + timedelta(seconds=30))
    with pytest.raises(InvalidModelError):
        running.heartbeat("worker-a", clock.value, clock.value)
    with pytest.raises(InvalidModelError):
        running.reclaim(clock.value, clock.value + timedelta(minutes=1))
    expired = replace(running, lease_expires_at=clock.value - timedelta(seconds=1))
    terminal = expired.reclaim(clock.value, clock.value + timedelta(minutes=1))
    assert terminal.status is IngestionJobStatus.FAILED
    assert terminal.last_error_code == "lease_expired_max_attempts"
    with pytest.raises(InvalidModelError):
        terminal.cancel(clock.value)
    with pytest.raises(InvalidModelError):
        IngestionJobEvent(
            UUID(int=1), job.job_id, 0, IngestionJobEventKind.ENQUEUED, clock.value, 0
        )
    with pytest.raises(InvalidModelError):
        replace(job, attempts=1)
    with pytest.raises(InvalidModelError):
        replace(
            job,
            last_error_code="speaker_review_failed",
            next_attempt_at=clock.value + timedelta(minutes=1),
        )
    with pytest.raises(InvalidModelError):
        replace(job, status=IngestionJobStatus.SUCCEEDED, finished_at=clock.value)


def test_service_cancel_reclaim_and_error_guards() -> None:
    clock = _Clock()
    service = IngestionJobService(InMemoryIngestionJobUnitOfWorkFactory(), clock)
    cancelled = service.enqueue(_command())
    assert service.cancel(cancelled.job_id).status is IngestionJobStatus.CANCELLED
    assert service.events(UUID(int=9999)) == ()
    with pytest.raises(ValueError):
        service.fail_or_retry(cancelled.job_id, "worker-a", "not-allowlisted")
    reclaimable = service.enqueue(
        EnqueueIngestionJob(
            kind=IngestionJobKind.SPEAKER_REVIEW,
            series_id=UUID(int=11),
            season_number=2,
            episode_number=1,
            source_fingerprint="c" * 64,
            pipeline_revision="phase27-v1",
            max_attempts=2,
        )
    )
    assert service.claim_next("worker-a") is not None
    clock.value += timedelta(minutes=11)
    reclaimed = service.reclaim_expired(reclaimable.job_id)
    assert reclaimed.status is IngestionJobStatus.PENDING
    retryable = service.enqueue(
        EnqueueIngestionJob(
            kind=IngestionJobKind.SPEAKER_REVIEW,
            series_id=UUID(int=11),
            season_number=2,
            episode_number=2,
            source_fingerprint="d" * 64,
            pipeline_revision="phase27-v1",
            max_attempts=2,
        )
    )
    assert service.claim_next("worker-a") is not None
    retried = service.fail_or_retry(retryable.job_id, "worker-a", "speaker_review_failed")
    cancelled_retry = service.cancel(retried.job_id)
    assert cancelled_retry.status is IngestionJobStatus.CANCELLED
    assert cancelled_retry.last_error_code is None
    assert cancelled_retry.next_attempt_at is None


def test_inventory_detail_is_safe_and_rejects_external_destination(tmp_path: Path) -> None:
    series_id = UUID(int=101)
    season_id = UUID(int=102)
    episodes = tuple(
        Episode(
            series_id,
            season_id,
            UUID(int=200 + number),
            number,
            f"Episode {number}",
            f"Modern Family - 1x0{number} - Episode {number}.reviewed.srt",
        )
        for number in (1, 2, 3)
    )
    manifest = CatalogueManifest(
        1, (Series(series_id, "Modern Family", (Season(series_id, season_id, 1, episodes),)),)
    )
    root = tmp_path / "corpus"
    reviewed = root / "Modern_Family - season 1.en" / "reviewed"
    aligned = root / "Modern_Family - season 1.en" / "script-aligned"
    reviewed.mkdir(parents=True)
    aligned.mkdir(parents=True)
    reviewed_file = reviewed / episodes[0].reviewed_subtitle_filename
    reviewed_file.write_text("synthetic", encoding="utf-8")
    import hashlib

    reviewed_hash = hashlib.sha256(b"synthetic").hexdigest()
    (reviewed / "review-ledger.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_status": "automated_reviewed",
                "records": [
                    {"reviewed_filename": reviewed_file.name, "reviewed_sha256": reviewed_hash}
                ],
            }
        ),
        encoding="utf-8",
    )
    (
        aligned
        / episodes[1].reviewed_subtitle_filename.replace(".reviewed.srt", ".script-aligned.srt")
    ).write_text("synthetic", encoding="utf-8")
    output = root / "reports" / "inventory.json"
    report = CorpusInventoryService().inspect(root, manifest, output)
    assert report.counts[CorpusReadinessStatus.REVIEWED_READY.value] == 1
    assert report.counts[CorpusReadinessStatus.AWAITING_AUTOMATED_REVIEW.value] == 1
    assert report.counts[CorpusReadinessStatus.MISSING.value] == 1
    assert "synthetic" not in output.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        CorpusInventoryService().inspect(root, manifest, tmp_path / "outside.json")


def test_inventory_rejects_duplicate_ledger_and_unsafe_series_locator(tmp_path: Path) -> None:
    series_id = UUID(int=301)
    season_id = UUID(int=302)
    episode = Episode(
        series_id,
        season_id,
        UUID(int=303),
        1,
        "Episode 1",
        "Modern Family - 1x01 - Episode 1.reviewed.srt",
    )
    root = tmp_path / "corpus"
    reviewed = root / "Modern_Family - season 1.en" / "reviewed"
    reviewed.mkdir(parents=True)
    reviewed_file = reviewed / episode.reviewed_subtitle_filename
    reviewed_file.write_text("synthetic", encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(b"synthetic").hexdigest()
    record = {"reviewed_filename": reviewed_file.name, "reviewed_sha256": digest}
    (reviewed / "review-ledger.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_status": "automated_reviewed",
                "records": [record, record],
            }
        ),
        encoding="utf-8",
    )
    manifest = CatalogueManifest(
        1,
        (Series(series_id, "Modern Family", (Season(series_id, season_id, 1, (episode,)),)),),
    )
    report = CorpusInventoryService().inspect(root, manifest)
    assert report.items[0].status is CorpusReadinessStatus.INVALID
    assert report.items[0].reason_code is CorpusInventoryReason.REVIEW_LEDGER_HASH_OR_SCOPE_MISMATCH

    unsafe_manifest = CatalogueManifest(
        1,
        (Series(series_id, "../unsafe", (Season(series_id, season_id, 1, (episode,)),)),),
    )
    unsafe_report = CorpusInventoryService().inspect(root, unsafe_manifest)
    assert unsafe_report.items[0].status is CorpusReadinessStatus.INVALID
    assert unsafe_report.items[0].reason_code is CorpusInventoryReason.UNSAFE_LOCATOR
    assert unsafe_report.items[0].relative_locator == ""
