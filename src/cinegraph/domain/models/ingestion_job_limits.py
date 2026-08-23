import re

MAX_INGESTION_JOB_ATTEMPTS = 20
MAX_INGESTION_JOB_PRIORITY = 100
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVISION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
WORKER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
LEASE_EXPIRED_ERROR_CODE = "lease_expired"
LEASE_EXPIRED_MAX_ATTEMPTS_ERROR_CODE = "lease_expired_max_attempts"
ALLOWED_INGESTION_ERROR_CODES = frozenset(
    {
        LEASE_EXPIRED_ERROR_CODE,
        LEASE_EXPIRED_MAX_ATTEMPTS_ERROR_CODE,
        "source_invalid",
        "alignment_failed",
        "speaker_review_failed",
        "transcript_ingestion_failed",
        "vector_index_failed",
        "episode_summary_failed",
        "series_metadata_failed",
        "unknown_retryable",
    }
)
