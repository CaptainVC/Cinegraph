from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelTokenPricing:
    input_usd_per_million: float
    output_usd_per_million: float


@dataclass(frozen=True, slots=True)
class SpeakerReviewConfiguration:
    schema_version: int
    ledger_schema_version: int
    prompt_version: str
    batch_endpoint: str
    batch_method: str
    response_schema_name: str
    custom_id_separator: str
    adjudication_pass_id: str
    final_review_pass_id: str
    final_review_retry_pass_id_template: str
    subtitle_context_radius: int
    script_match_limit: int
    script_context_radius: int
    primary_pass_ids: tuple[str, ...]
    consensus_minimum_confidence: float
    adjudication_minimum_confidence: float
    final_review_minimum_confidence: float
    max_output_tokens: int
    adjudication_max_output_tokens: int
    final_review_max_output_tokens: int
    final_review_retry_max_output_tokens: int
    final_review_max_retry_rounds: int
    estimated_characters_per_token: int
    maximum_enqueued_input_tokens_per_batch: int
    batch_discount_multiplier: float
    maximum_run_cost_usd: float
    batch_completion_window: str
    poll_interval_seconds: int
    maximum_wait_seconds: int
    calibration_sample_size: int
    script_pdf_filename_template: str
    season_directory_glob_template: str
    aligned_subtitle_glob: str
    run_directory_name: str
    successful_batch_status: str
    terminal_batch_failure_statuses: frozenset[str]
    redaction_placeholders: frozenset[str]
    model_pricing: dict[str, ModelTokenPricing]


DEFAULT_SPEAKER_REVIEW_CONFIGURATION = SpeakerReviewConfiguration(
    schema_version=4,
    ledger_schema_version=4,
    prompt_version="speaker-review-v1",
    batch_endpoint="/v1/responses",
    batch_method="POST",
    response_schema_name="speaker_review_verdict",
    custom_id_separator="::",
    adjudication_pass_id="adjudication",
    final_review_pass_id="final-review",
    final_review_retry_pass_id_template="final-review-retry-{round_number}",
    subtitle_context_radius=3,
    script_match_limit=3,
    script_context_radius=2,
    primary_pass_ids=("primary-a", "primary-b"),
    consensus_minimum_confidence=0.97,
    adjudication_minimum_confidence=0.95,
    final_review_minimum_confidence=0.90,
    max_output_tokens=320,
    adjudication_max_output_tokens=640,
    final_review_max_output_tokens=1_200,
    final_review_retry_max_output_tokens=2_400,
    final_review_max_retry_rounds=1,
    estimated_characters_per_token=3,
    maximum_enqueued_input_tokens_per_batch=1_500_000,
    batch_discount_multiplier=0.5,
    maximum_run_cost_usd=5.0,
    batch_completion_window="24h",
    poll_interval_seconds=30,
    maximum_wait_seconds=86_400,
    calibration_sample_size=100,
    script_pdf_filename_template="Modern Family S{season:02d} Script.pdf",
    season_directory_glob_template="*season {season}.en",
    aligned_subtitle_glob="*.script-aligned.srt",
    run_directory_name="review-runs",
    successful_batch_status="completed",
    terminal_batch_failure_statuses=frozenset({"failed", "expired", "cancelled"}),
    redaction_placeholders=frozenset({"***", "- ***.", "--"}),
    model_pricing={
        "gpt-5.6-luna": ModelTokenPricing(
            input_usd_per_million=0.20,
            output_usd_per_million=1.20,
        ),
        "gpt-5.6-terra": ModelTokenPricing(
            input_usd_per_million=2.00,
            output_usd_per_million=12.00,
        ),
        "gpt-5.6-sol": ModelTokenPricing(
            input_usd_per_million=5.00,
            output_usd_per_million=30.00,
        ),
    },
)
