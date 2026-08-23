class TranscriptErrorMessages:
    TRANSCRIPT_CHUNK_SEGMENTS_MUST_BE_NONEMPTY_UNIQUE = (
        "Transcript chunk member segment IDs must be non-empty and unique."
    )
    TRANSCRIPT_CHUNK_SOURCE_FIELDS_MUST_BE_VALID = (
        "Transcript chunk source, episode, language, and rights fields must be valid."
    )
    TRANSCRIPT_CHUNK_TIMING_MUST_BE_VALID = "Transcript chunk timing must be valid."
    TRANSCRIPT_CHUNK_TEXT_MUST_BE_TRIMMED = "Transcript chunk text must be non-empty and trimmed."
    TRANSCRIPT_CHUNK_ORDINAL_MUST_BE_NON_NEGATIVE = "Transcript chunk ordinal must be non-negative."
    TRANSCRIPT_CHUNK_REVISION_MUST_BE_TRIMMED = (
        "Transcript chunking revision must be non-empty and trimmed."
    )
    TRANSCRIPT_CHUNK_INPUT_MUST_BE_CHRONOLOGICAL = (
        "Transcript chunk input must be chronological and governed consistently."
    )
    TRANSCRIPT_CHUNK_CONFIGURATION_INVALID = "Transcript chunking configuration values are invalid."
    TRANSCRIPT_CHUNK_SEGMENT_EXCEEDS_CHARACTER_LIMIT = (
        "A transcript cue cannot exceed the configured chunk character limit."
    )
    SOURCE_VERSION_MUST_BE_ACTIVE_AND_REVIEWED = (
        "Transcript source version must be active and approved."
    )
    SUBTITLE_INGESTION_REQUIRES_APPROVED_REVIEW_STATUS = (
        "Reviewed subtitle ingestion requires an approved review status."
    )
    TRANSCRIPT_SEGMENT_SOURCE_VERSION_MUST_MATCH = (
        "Transcript segment source version must match the command source version."
    )
    TRANSCRIPT_SEGMENT_IDS_MUST_BE_UNIQUE = "Transcript segment IDs must be unique."
    TRANSCRIPT_SEGMENT_RIGHTS_STATUS_MUST_MATCH = (
        "Transcript segment rights status must be allowed and match the source version."
    )
    TRANSCRIPT_SOURCE_RIGHTS_STATUS_MUST_BE_ALLOWED = (
        "Transcript source version rights status must be allowed."
    )
    TRANSCRIPT_INDEX_VECTOR_CARDINALITY_MUST_MATCH = (
        "Transcript index vector cardinality must match chunk cardinality."
    )
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
