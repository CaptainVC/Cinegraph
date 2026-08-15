from cinegraph.ingestion.transcript_srt.constants import SrtConstants
from pathlib import Path

from cinegraph.common.error_messages import SubtitleErrorMessages
from cinegraph.ingestion.transcript_srt.models import ParsedSrtCue
from cinegraph.ingestion.transcript_srt.patterns import SrtPatterns

# Read SRT text using the supported encodings, raising a domain error if none work.
def read_srt_text(source_path: Path) -> str:
    for encoding in SrtConstants.SRT_ENCODINGS:
        try:
            return source_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    raise ValueError(
        SubtitleErrorMessages.SUBTITLE_FILE_DECODE_FAILED.format(
            subtitle_path=source_path
        )
    )


# Split canonical SRT text into non-empty blocks and parse each cue.
def parse_srt(text: str) -> tuple[ParsedSrtCue, ...]:
    return tuple(
        _parse_cue(block)
        for block in SrtPatterns.CUE_SEPARATOR.split(text.strip())
        if block.strip()
    )


# Validate one SRT block and convert its number, time range, and dialogue lines.
def _parse_cue(block: str) -> ParsedSrtCue:

    # Split the block and parse its first line as a positive cue number.
    lines = block.splitlines()

    # Require a timecode line after the cue number.
    cue_number = _parse_cue_number(lines[0])

    # Match the SRT timecode format before converting either timestamp.
    if len(lines) < 2:
        raise ValueError(
            SubtitleErrorMessages.SRT_CUE_MUST_HAVE_A_TIMECODE.format(
                cue_number=cue_number
            )
        )

    # Reject malformed timecodes before reading their timestamp groups.
    match = SrtPatterns.TIMECODE.fullmatch(lines[1].strip())

    # Convert both timestamps and reject out-of-order cue boundaries.
    if match is None:
        raise ValueError(
            SubtitleErrorMessages.SRT_TIMECODE_IS_INVALID.format(
                cue_number=cue_number
            )
        )

    # Convert the captured timestamp groups to milliseconds.
    start_ms = _timestamp_ms(match.group(SrtConstants.TIMESTAMP_START), cue_number)
    end_ms = _timestamp_ms(match.group(SrtConstants.TIMESTAMP_END), cue_number)

    # Require a positive cue duration.
    if end_ms <= start_ms:
        raise ValueError(
            SubtitleErrorMessages.SRT_CUE_END_MUST_FOLLOW_START.format(
                cue_number=cue_number
            )
        )

    # Retain non-empty dialogue lines and reject cues without dialogue.
    dialogue_lines = tuple(line.strip() for line in lines[2:] if line.strip())
    if not dialogue_lines:
        raise ValueError(
            SubtitleErrorMessages.SRT_CUE_MUST_HAVE_DIALOGUE.format(
                cue_number=cue_number
            )
        )

    # Return the validated cue with normalized line boundaries.
    return ParsedSrtCue(
        cue_number=cue_number,
        start_ms=start_ms,
        end_ms=end_ms,
        lines=dialogue_lines,
    )


# Parse and validate the positive integer cue number.
def _parse_cue_number(value: str) -> int:
    try:
        cue_number = int(value.strip())
    except ValueError as error:
        raise ValueError(
            SubtitleErrorMessages.SRT_CUE_NUMBER_MUST_BE_POSITIVE
        ) from error

    if cue_number < 1:
        raise ValueError(SubtitleErrorMessages.SRT_CUE_NUMBER_MUST_BE_POSITIVE)

    return cue_number


# Convert an SRT timestamp component to milliseconds and validate its ranges.
def _timestamp_ms(value: str, cue_number: int) -> int:
    hours, minutes, seconds_and_ms = value.split(":")
    seconds, milliseconds = seconds_and_ms.split(",")

    parsed_hours = int(hours)
    parsed_minutes = int(minutes)
    parsed_seconds = int(seconds)
    parsed_milliseconds = int(milliseconds)

    if (
        parsed_minutes > SrtConstants.MINS_UPPER_LIMIT
        or parsed_seconds > SrtConstants.SECS_UPPER_LIMIT
        or parsed_milliseconds > SrtConstants.MS_UPPER_LIMIT
    ):
        raise ValueError(
            SubtitleErrorMessages.SRT_TIMECODE_IS_INVALID.format(
                cue_number=cue_number
            )
        )

    return (
        parsed_hours * SrtConstants.MS_PER_HOUR
        + parsed_minutes * SrtConstants.MS_PER_MINUTE
        + parsed_seconds * SrtConstants.MS_PER_SECOND
        + parsed_milliseconds
    )
