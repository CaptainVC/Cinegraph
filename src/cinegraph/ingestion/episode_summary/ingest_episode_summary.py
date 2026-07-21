
from dataclasses import dataclass
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.models.episode_summary.episode_summary_document import EpisodeSummaryDocument
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef

@dataclass(frozen=True, slots=True)
class IngestEpisodeSummaryCommand:
    source_document: SourceDocument
    page_title: str
    episode: EpisodeRef
    language: Language
    rights_status: RightsStatus

@dataclass(frozen=True, slots=True)
class IngestEpisodeSummaryResult:
   source_version: SourceVersion
   summary: EpisodeSummaryDocument | None
   was_already_ingested: bool
