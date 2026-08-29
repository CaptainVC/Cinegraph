from dataclasses import dataclass
from re import compile as compile_pattern

from cinegraph.common.error_messages import ConfigurationErrorMessages
from cinegraph.config.series_metadata import (
    SERIES_METADATA_APPROVED_DIRECTORY,
    SERIES_METADATA_ARTWORK_DIRECTORY,
    SERIES_METADATA_POSTER_MAX_BYTES,
    SERIES_METADATA_REVIEWER_ID,
)

API_PREFIX_PATTERN = compile_pattern(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*")
PRODUCT_UI_CLIENT_CONFIGURATION_PATH = "/client-config"
RESERVED_API_PREFIX_ROOTS = (
    "/assets",
    "/health",
    PRODUCT_UI_CLIENT_CONFIGURATION_PATH,
)
NONCANONICAL_API_PREFIX_SEGMENTS = frozenset({".", ".."})
API_SINGLE_PROCESS_WORKERS = 1


@dataclass(frozen=True, slots=True)
class ApiConfiguration:
    title: str
    version: str
    api_prefix: str
    catalogue_manifest_filename: str
    series_metadata_approved_directory: str
    series_artwork_directory: str
    maximum_series_poster_bytes: int
    series_poster_cache_control: str
    automated_metadata_reviewer: str
    minimum_question_length: int
    maximum_question_length: int
    default_retrieval_limit: int
    maximum_retrieval_limit: int
    request_id_header: str
    maximum_request_body_bytes: int
    rate_limit_capacity: int
    rate_limit_refill_per_second: float
    rate_limit_bucket_idle_ttl_seconds: int
    maximum_rate_limit_buckets: int
    default_request_cost: int
    health_request_cost: int
    authentication_request_cost: int
    chat_request_cost: int
    static_asset_request_cost: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.api_prefix, str)
            or API_PREFIX_PATTERN.fullmatch(self.api_prefix) is None
        ):
            raise ValueError(ConfigurationErrorMessages.API_PREFIX_SHAPE)
        if any(
            segment in NONCANONICAL_API_PREFIX_SEGMENTS
            for segment in self.api_prefix.split("/")[1:]
        ):
            raise ValueError(ConfigurationErrorMessages.API_PREFIX_DOT_SEGMENTS)
        if any(
            self.api_prefix == root or self.api_prefix.startswith(f"{root}/")
            for root in RESERVED_API_PREFIX_ROOTS
        ):
            raise ValueError(ConfigurationErrorMessages.API_PREFIX_RESERVED)
        positive_integers = (
            self.maximum_request_body_bytes,
            self.maximum_series_poster_bytes,
            self.rate_limit_capacity,
            self.rate_limit_bucket_idle_ttl_seconds,
            self.maximum_rate_limit_buckets,
        )
        if any(value < 1 for value in positive_integers):
            raise ValueError("API guardrail limits must be positive.")
        if self.rate_limit_refill_per_second <= 0:
            raise ValueError("Rate-limit refill rate must be positive.")
        costs = (
            self.default_request_cost,
            self.health_request_cost,
            self.authentication_request_cost,
            self.chat_request_cost,
            self.static_asset_request_cost,
        )
        if any(cost < 0 or cost > self.rate_limit_capacity for cost in costs):
            raise ValueError("Request costs must fit within rate-limit capacity.")
        if not self.request_id_header or self.request_id_header.strip() != self.request_id_header:
            raise ValueError("Request ID header must be non-empty and trimmed.")
        for value in (
            self.series_metadata_approved_directory,
            self.series_artwork_directory,
            self.series_poster_cache_control,
            self.automated_metadata_reviewer,
        ):
            if not value or value.strip() != value:
                raise ValueError("Metadata publication configuration must be trimmed.")


DEFAULT_API_CONFIGURATION = ApiConfiguration(
    title="Cinegraph API",
    version="1.0.0",
    api_prefix="/api/v1",
    catalogue_manifest_filename="catalogue.json",
    series_metadata_approved_directory=SERIES_METADATA_APPROVED_DIRECTORY,
    series_artwork_directory=SERIES_METADATA_ARTWORK_DIRECTORY,
    maximum_series_poster_bytes=SERIES_METADATA_POSTER_MAX_BYTES,
    series_poster_cache_control="private, max-age=86400",
    automated_metadata_reviewer=SERIES_METADATA_REVIEWER_ID,
    minimum_question_length=2,
    maximum_question_length=2_000,
    default_retrieval_limit=8,
    maximum_retrieval_limit=20,
    request_id_header="X-Request-ID",
    maximum_request_body_bytes=64 * 1024,
    rate_limit_capacity=120,
    rate_limit_refill_per_second=2.0,
    rate_limit_bucket_idle_ttl_seconds=60 * 60,
    maximum_rate_limit_buckets=10_000,
    default_request_cost=1,
    health_request_cost=0,
    authentication_request_cost=5,
    chat_request_cost=10,
    static_asset_request_cost=0,
)
