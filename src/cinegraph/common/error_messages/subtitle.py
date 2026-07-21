class SubtitleErrorMessages:
    PDF_TEXT_EXTRACTION_FAILED = (
        "No text could be extracted from {pdf_path}. Use OCR before alignment."
    )
    SUBTITLE_FILE_DECODE_FAILED = "Cannot decode subtitle file: {subtitle_path}"
    SUBTITLE_ALIGNMENT_BACKTRACE_FAILED = "Subtitle alignment backtrace failed."
    SUBTITLE_EPISODE_NOT_FOUND = (
        "Cannot determine season and episode from {subtitle_name}"
    )
    SCRIPT_DIALOGUE_NOT_FOUND = (
        "No script dialogue found for {season}x{episode:02d}."
    )
    ORDERED_SCRIPT_FALLBACK_NOT_FOUND = (
        "No ordered script fallback found for {subtitle_path}:{line_number}"
    )
    SRT_CUE_NUMBER_MUST_BE_POSITIVE = "SRT cue numbers must be positive integers."
    SRT_CUE_MUST_HAVE_A_TIMECODE = "SRT cue {cue_number} must have a timecode."
    SRT_TIMECODE_IS_INVALID = "SRT cue {cue_number} has an invalid timecode."
    SRT_CUE_END_MUST_FOLLOW_START = (
        "SRT cue {cue_number} must end after it starts."
    )
    SRT_CUE_MUST_HAVE_DIALOGUE = "SRT cue {cue_number} must contain dialogue."
    SRT_CUE_REQUIRES_VERIFIED_SPEAKER_LABEL = (
        "SRT cue {cue_number} contains a line without a verified speaker label."
    )
    REVIEWED_BY_MUST_BE_TRIMMED = (
        "reviewed_by must be a non-empty trimmed string."
    )
    NO_SCRIPT_ALIGNED_SRT_FILES_FOUND = (
        "No script-aligned SRT files were found for promotion."
    )
    MALFORMED_SRT_CUE_FOR_PROMOTION = "Malformed SRT cue in {filename}."
    UNREVIEWED_SUBTITLE_LINE_FOR_PROMOTION = (
        "Unreviewed subtitle line in {filename}, cue {cue_number}: {line!r}"
    )
    REVIEWED_SRT_CONTENT_CONFLICT = (
        "Refusing to overwrite reviewed source with different content: {path}"
    )
