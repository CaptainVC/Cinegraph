from cinegraph.ingestion.transcript_srt.constants import SrtConstants
from pathlib import Path

from cinegraph.common.error_messages import SubtitleErrorMessages
from cinegraph.ingestion.transcript_srt.models import ParsedSrtCue
from cinegraph.ingestion.transcript_srt.patterns import SrtPatterns

# Reads and returns the requested source content.
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


# Parses the supplied text into structured values.
def parse_srt(text: str) -> tuple[ParsedSrtCue, ...]:
    return tuple(
        _parse_cue(block)
        for block in SrtPatterns.CUE_SEPARATOR.split(text.strip())
        if block.strip()
    )


# Parses the supplied text into structured values.
def _parse_cue(block: str) -> ParsedSrtCue:

    # 1. Split the block into lines and extract the cue number
    lines = block.splitlines()

    # 2. Validate the cue number and timecode
    cue_number = _parse_cue_number(lines[0])

    # 3. Validate the timecode and dialogue lines
    if len(lines) < 2:
        raise ValueError(
            SubtitleErrorMessages.SRT_CUE_MUST_HAVE_A_TIMECODE.format(
                cue_number=cue_number
            )
        )

    # 4. Validate the timecode format and extract start and end times
    match = SrtPatterns.TIMECODE.fullmatch(lines[1].strip())

    # 5. Validate the timecode values and ensure end time is after start time
    if match is None:
        raise ValueError(
            SubtitleErrorMessages.SRT_TIMECODE_IS_INVALID.format(
                cue_number=cue_number
            )
        )

    # 6. Extract start and end times
    start_ms = _timestamp_ms(match.group(SrtConstants.TIMESTAMP_START), cue_number)
    end_ms = _timestamp_ms(match.group(SrtConstants.TIMESTAMP_END), cue_number)

    # 7. Validate that the end time is after the start time
    if end_ms <= start_ms:
        raise ValueError(
            SubtitleErrorMessages.SRT_CUE_END_MUST_FOLLOW_START.format(
                cue_number=cue_number
            )
        )

    # 8. Extract dialogue lines and ensure they are not empty
    dialogue_lines = tuple(line.strip() for line in lines[2:] if line.strip())
    if not dialogue_lines:
        raise ValueError(
            SubtitleErrorMessages.SRT_CUE_MUST_HAVE_DIALOGUE.format(
                cue_number=cue_number
            )
        )

    # 9. Return the parsed SRT cue as a ParsedSrtCue object
    return ParsedSrtCue(
        cue_number=cue_number,
        start_ms=start_ms,
        end_ms=end_ms,
        lines=dialogue_lines,
    )


# Parses the supplied text into structured values.
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


# Processes the supplied timestamp ms values.
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
