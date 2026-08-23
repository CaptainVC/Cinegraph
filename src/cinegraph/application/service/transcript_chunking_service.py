from cinegraph.common.error_messages import TranscriptErrorMessages
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config import (
    DEFAULT_TRANSCRIPT_CHUNKING_CONFIGURATION,
    TranscriptChunkingConfiguration,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.transcript import TranscriptRetrievalChunk, TranscriptSegment


class TranscriptChunkingService:
    """Create deterministic, speaker-aware evidence chunks from ordered cues."""

    def __init__(
        self,
        configuration: TranscriptChunkingConfiguration = DEFAULT_TRANSCRIPT_CHUNKING_CONFIGURATION,
    ) -> None:
        self._configuration = configuration

    def chunk(
        self, segments: tuple[TranscriptSegment, ...]
    ) -> tuple[TranscriptRetrievalChunk, ...]:
        if not isinstance(segments, tuple):
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_CHUNK_INPUT_MUST_BE_CHRONOLOGICAL
            )
        if not segments:
            return ()
        self._validate_input(segments)
        chunks: list[TranscriptRetrievalChunk] = []
        start = 0
        ordinal = 0
        while start < len(segments):
            end = start + 1
            split_reason = "end"
            while end < len(segments):
                candidate = segments[end]
                if (
                    candidate.start_ms - max(item.end_ms for item in segments[start:end])
                    > self._configuration.max_inter_segment_gap_ms
                ):
                    split_reason = "gap"
                    break
                members = segments[start : end + 1]
                if len(members) > self._configuration.max_segments:
                    split_reason = "bound"
                    break
                if (
                    max(item.end_ms for item in members) - members[0].start_ms
                    > self._configuration.max_duration_ms
                ):
                    split_reason = "bound"
                    break
                rendered = self._render(members)
                if len(rendered) > self._configuration.max_characters and len(members) > 1:
                    split_reason = "bound"
                    break
                end += 1
            members = segments[start:end]
            text = self._render(members)
            chunks.append(self._make_chunk(members, ordinal, text))
            ordinal += 1
            if end >= len(segments):
                break
            if split_reason == "gap":
                start = end
            else:
                start = max(start + 1, end - self._configuration.overlap_segments)
        return tuple(chunks)

    def _validate_input(self, segments: tuple[TranscriptSegment, ...]) -> None:
        first = segments[0]
        seen = set()
        previous_start = -1
        previous_end = -1
        previous_id = ""
        for segment in segments:
            if len(self._render((segment,))) > self._configuration.max_characters:
                raise InvalidModelError(
                    TranscriptErrorMessages.TRANSCRIPT_CHUNK_SEGMENT_EXCEEDS_CHARACTER_LIMIT
                )
            if segment.segment_id in seen or (
                segment.start_ms < previous_start
                or (
                    segment.start_ms == previous_start
                    and (segment.end_ms, str(segment.segment_id)) < (previous_end, previous_id)
                )
            ):
                raise InvalidModelError(
                    TranscriptErrorMessages.TRANSCRIPT_CHUNK_INPUT_MUST_BE_CHRONOLOGICAL
                )
            seen.add(segment.segment_id)
            previous_start = segment.start_ms
            previous_end = segment.end_ms
            previous_id = str(segment.segment_id)
            if (
                segment.source_version_id != first.source_version_id
                or segment.episode != first.episode
                or segment.language is not first.language
                or segment.rights_status is not first.rights_status
            ):
                raise InvalidModelError(
                    TranscriptErrorMessages.TRANSCRIPT_CHUNK_INPUT_MUST_BE_CHRONOLOGICAL
                )

    @staticmethod
    def _render(segments: tuple[TranscriptSegment, ...] | list[TranscriptSegment]) -> str:
        lines = []
        for segment in segments:
            names = []
            for candidate in segment.speaker_candidates:
                if candidate.name not in names:
                    names.append(candidate.name)
            prefix = f"{' / '.join(names)}: " if names else ""
            lines.append(prefix + segment.text)
        return "\n".join(lines).strip()

    def _make_chunk(
        self,
        members: tuple[TranscriptSegment, ...],
        ordinal: int,
        text: str,
    ) -> TranscriptRetrievalChunk:
        first = members[0]
        ids = tuple(segment.segment_id for segment in members)
        return TranscriptRetrievalChunk(
            chunk_id=IdentifierGenerator.transcript_chunk_id(
                self._configuration.revision,
                first.source_version_id,
                first.episode.series_id,
                first.episode.season_id,
                first.episode.episode_id,
                ids,
            ),
            source_version_id=first.source_version_id,
            episode=first.episode,
            ordinal=ordinal,
            member_segment_ids=ids,
            start_ms=first.start_ms,
            end_ms=max(item.end_ms for item in members),
            text=text,
            language=first.language,
            rights_status=first.rights_status,
            index_revision=self._configuration.revision,
        )
