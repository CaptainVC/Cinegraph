class TranscriptErrorMessages:
    SPEAKER_CANDIDATE_NAME_MUST_BE_TRIMMED = (
        "Speaker candidate name cannot be empty or have leading/trailing whitespace."
    )
    SPEAKER_CANDIDATE_CONFIDENCE_MUST_BE_FINITE = (
        "Speaker candidate confidence must be a finite value from 0.0 through 1.0."
    )
    TRANSCRIPT_SEGMENT_START_MS_MUST_BE_NON_NEGATIVE = (
        "Transcript segment start_ms must be non-negative."
    )
    TRANSCRIPT_SEGMENT_END_MS_MUST_FOLLOW_START_MS = (
        "Transcript segment end_ms must be greater than start_ms."
    )
    TRANSCRIPT_SEGMENT_TEXT_MUST_BE_TRIMMED = (
        "Transcript segment text cannot be empty or have leading/trailing whitespace."
    )
    TRANSCRIPT_SEGMENT_LANGUAGE_MUST_BE_SUPPORTED = (
        "Transcript segment language must be a supported Language value."
    )
    TRANSCRIPT_SEGMENT_SPEAKER_CANDIDATES_MUST_BE_IMMUTABLE = (
        "Transcript segment speaker candidates must be an immutable tuple."
    )
    TRANSCRIPT_SEGMENT_CANNOT_HAVE_DUPLICATE_SPEAKER_CANDIDATES = (
        "Transcript segment cannot contain duplicate speaker candidate names."
    )
