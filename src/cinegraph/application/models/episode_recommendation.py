from dataclasses import dataclass
from uuid import UUID

from cinegraph.domain.enums.enum import WatchPreference
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodeRef, ProfileWatchState
from cinegraph.ports.retrieval import RetrievedSegment


@dataclass(frozen=True, slots=True)
class RecommendEpisodesQuery:
    series_id: UUID
    mood: str
    characters: tuple[str, ...]
    excluded_themes: tuple[str, ...]
    watch_preference: WatchPreference
    requested_count: int
    profile_watch_state: ProfileWatchState | None
    corpus_access_scope: CorpusAccessScope
    maximum_runtime_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class RecommendationCandidate:
    episode: EpisodeRef
    episode_title: str | None
    synopsis: str | None
    runtime_seconds: int | None
    evidence: tuple[RetrievedSegment, ...] = ()


@dataclass(frozen=True, slots=True)
class RecommendationRankingRequest:
    mood: str
    characters: tuple[str, ...]
    excluded_themes: tuple[str, ...]
    requested_count: int
    candidates: tuple[RecommendationCandidate, ...]


@dataclass(frozen=True, slots=True)
class RankedRecommendationDraft:
    episode_id: UUID
    score: float
    reason: str
    cited_segment_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class EpisodeRecommendation:
    episode: EpisodeRef
    episode_title: str | None
    runtime_seconds: int | None
    score: float
    reason: str
    citations: tuple[RetrievedSegment, ...]


@dataclass(frozen=True, slots=True)
class RecommendEpisodesResult:
    recommendations: tuple[EpisodeRecommendation, ...]
    visible_candidate_count: int
