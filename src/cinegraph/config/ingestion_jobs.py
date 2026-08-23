from dataclasses import dataclass
from datetime import timedelta

from cinegraph.common.error_messages import IngestionJobErrorMessages
from cinegraph.domain.models.ingestion_job_limits import (
    ALLOWED_INGESTION_ERROR_CODES,
    MAX_INGESTION_JOB_ATTEMPTS,
)

MAX_INGESTION_JOB_CLAIM_BATCH_SIZE = 100

__all__ = [
    "ALLOWED_INGESTION_ERROR_CODES",
    "DEFAULT_INGESTION_JOB_CONFIGURATION",
    "IngestionJobConfiguration",
    "MAX_INGESTION_JOB_CLAIM_BATCH_SIZE",
]


@dataclass(frozen=True, slots=True)
class IngestionJobConfiguration:
    lease_duration: timedelta
    heartbeat_extension: timedelta
    retry_base_delay: timedelta
    retry_max_delay: timedelta
    default_max_attempts: int
    claim_batch_size: int

    def __post_init__(self) -> None:
        delays = (
            self.lease_duration,
            self.heartbeat_extension,
            self.retry_base_delay,
            self.retry_max_delay,
        )
        if any(delay <= timedelta(0) for delay in delays):
            raise ValueError(IngestionJobErrorMessages.CONFIGURATION_INVALID)
        if self.retry_base_delay > self.retry_max_delay:
            raise ValueError(IngestionJobErrorMessages.CONFIGURATION_INVALID)
        if not 1 <= self.default_max_attempts <= MAX_INGESTION_JOB_ATTEMPTS:
            raise ValueError(IngestionJobErrorMessages.CONFIGURATION_INVALID)
        if not 1 <= self.claim_batch_size <= MAX_INGESTION_JOB_CLAIM_BATCH_SIZE:
            raise ValueError(IngestionJobErrorMessages.CONFIGURATION_INVALID)


DEFAULT_INGESTION_JOB_CONFIGURATION = IngestionJobConfiguration(
    lease_duration=timedelta(minutes=10),
    heartbeat_extension=timedelta(minutes=10),
    retry_base_delay=timedelta(minutes=1),
    retry_max_delay=timedelta(hours=1),
    default_max_attempts=3,
    claim_batch_size=1,
)
