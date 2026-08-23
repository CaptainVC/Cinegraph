from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cinegraph.domain.enums.enum import (
    CorpusInventoryReason,
    CorpusReadinessStatus,
    IngestionJobKind,
)


@dataclass(frozen=True, slots=True)
class EnqueueIngestionJob:
    kind: IngestionJobKind
    series_id: UUID
    source_fingerprint: str
    pipeline_revision: str
    season_number: int | None = None
    episode_number: int | None = None
    priority: int = 0
    scheduled_at: datetime | None = None
    max_attempts: int | None = None


@dataclass(frozen=True, slots=True)
class IngestionJobPlanItem:
    episode_id: UUID
    season_number: int
    episode_number: int
    status: CorpusReadinessStatus
    reason_code: CorpusInventoryReason
    relative_locator: str
    content_sha256: str | None


@dataclass(frozen=True, slots=True)
class IngestionInventoryReport:
    counts: dict[str, int]
    items: tuple[IngestionJobPlanItem, ...]
