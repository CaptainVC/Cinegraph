class SpeakerReviewErrorMessages:
    NO_UNCERTAIN_SPEAKER_LABELS = "No uncertain speaker labels were found."
    SCRIPT_DIALOGUE_REQUIRED = "Script dialogue is required for {season}x{episode:02d}."
    CANDIDATE_IDENTIFIER_MISMATCH = (
        "Model response candidate ID does not match the batch request."
    )
    MODEL_SPEAKER_NOT_ALLOWED = "Model selected a speaker outside the episode allowlist."
    MODEL_EVIDENCE_NOT_ALLOWED = "Model cited evidence outside the supplied evidence set."
    MODEL_RESPONSE_MALFORMED = "The model response did not contain valid structured output."
    BATCH_REQUEST_FAILED = "OpenAI Batch request failed for {custom_id}."
    BATCH_NOT_COMPLETE = "OpenAI Batch {batch_id} is not complete (status={status})."
    BATCH_TERMINAL_FAILURE = "OpenAI Batch {batch_id} ended with status {status}."
    REVIEW_BUDGET_EXCEEDED = (
        "Estimated review cost ${estimated:.4f} exceeds the ${maximum:.2f} limit."
    )
    MODEL_PRICING_NOT_CONFIGURED = (
        "No centralized Batch token pricing is configured for model {model}."
    )
    BATCH_REQUEST_TOKEN_LIMIT_EXCEEDED = (
        "One Batch request exceeds the configured enqueued-token limit."
    )
    REVIEW_DECISION_MISSING = "No final speaker decision exists for {candidate_id}."
    REVIEWED_OUTPUT_CONFLICT = "Refusing to overwrite different reviewed output: {path}"
    RUN_STATE_CONFLICT = "Review run state is incompatible with this operation: {status}."
    ACTIVE_BATCH_ID_MISSING = (
        "{stage} review state does not contain the active Batch ID."
    )
    OPENAI_KEY_NOT_FOUND = "No labelled OPENAI_API_KEY was found in the source file."
    OPENAI_KEY_DUPLICATED = "More than one OPENAI_API_KEY was found in the source file."
    SECRET_DESTINATION_NOT_PRIVATE = "Secret destination permissions are not private."
    OPENAI_AUTHENTICATION_FAILED = (
        "OpenAI rejected the configured API key; replace OPENAI_API_KEY before retrying."
    )
