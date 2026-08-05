from typing import Protocol

from cinegraph.domain.models.transcript.transcript_segment import (
    TranscriptSegment,
)
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


class TranscriptSegmentReader(Protocol):
    def get_active_reviewed_segments(
        self,
        episode: EpisodeRef,
    ) -> tuple[TranscriptSegment, ...]: ...
