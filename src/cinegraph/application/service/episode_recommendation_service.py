import math
from dataclasses import replace

from cinegraph.application.models.episode_recommendation import (
    EpisodeRecommendation,
    RankedRecommendationDraft,
    RecommendEpisodesQuery,
    RecommendEpisodesResult,
    RecommendationCandidate,
    RecommendationRankingRequest,
)
from cinegraph.application.models.search_visible_hybrid_segments import (
    SearchVisibleHybridSegmentsQuery,
)
from cinegraph.application.service.search_visible_hybrid_segments_service import (
    SearchVisibleHybridSegmentsService,
)
from cinegraph.common.error_messages import RecommendationErrorMessages
from cinegraph.config import (
    DEFAULT_RECOMMENDATION_CONFIGURATION,
    RecommendationConfiguration,
)
from cinegraph.domain.enums.enum import WatchPreference
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.ports.recommendation import EpisodeRecommendationRanker


class EpisodeRecommendationService:
    def __init__(
        self,
        catalogue: CatalogueManifest,
        search: SearchVisibleHybridSegmentsService,
        ranker: EpisodeRecommendationRanker,
        spoiler_policy: SpoilerPolicy | None = None,
        configuration: RecommendationConfiguration = (
            DEFAULT_RECOMMENDATION_CONFIGURATION
        ),
    ) -> None:
        self._catalogue = catalogue
        self._search = search
        self._ranker = ranker
        self._spoiler_policy = spoiler_policy or SpoilerPolicy()
        self._configuration = configuration

    def filter_candidates(
        self,
        query: RecommendEpisodesQuery,
    ) -> tuple[RecommendationCandidate, ...]:
        self._validate_query(query)
        series = next(
            (item for item in self._catalogue.series if item.series_id == query.series_id),
            None,
        )
        if series is None:
            raise ValueError(RecommendationErrorMessages.SERIES_MUST_EXIST)
        refs_by_id = {
            item.episode_id: item for item in self._catalogue.episode_refs()
        }
        candidates: list[RecommendationCandidate] = []
        for season in series.seasons:
            for episode in season.episodes:
                episode_ref = refs_by_id[episode.episode_id]
                if not query.corpus_access_scope.allows_episode(episode_ref):
                    continue
                if not self._spoiler_policy.can_access(
                    (episode_ref,), query.profile_watch_state
                ):
                    continue
                watched = bool(
                    query.profile_watch_state
                    and query.profile_watch_state.is_episode_fully_watched(episode_ref)
                )
                if (
                    query.watch_preference is WatchPreference.WATCHED and not watched
                ) or (
                    query.watch_preference is WatchPreference.UNWATCHED and watched
                ):
                    continue
                if (
                    query.maximum_runtime_seconds is not None
                    and episode.runtime_seconds is not None
                    and episode.runtime_seconds > query.maximum_runtime_seconds
                ):
                    continue
                if self._contains_excluded_theme(
                    " ".join(
                        value
                        for value in (episode.episode_title, episode.synopsis)
                        if value
                    ),
                    query.excluded_themes,
                ):
                    continue
                candidates.append(
                    RecommendationCandidate(
                        episode=episode_ref,
                        episode_title=episode.episode_title,
                        synopsis=episode.synopsis,
                        runtime_seconds=episode.runtime_seconds,
                    )
                )
        return tuple(candidates)

    def retrieve_candidate_evidence(
        self,
        query: RecommendEpisodesQuery,
        candidates: tuple[RecommendationCandidate, ...],
    ) -> tuple[RecommendationCandidate, ...]:
        if not candidates:
            return ()
        search_result = self._search.execute(
            SearchVisibleHybridSegmentsQuery(
                query=self._render_retrieval_query(query),
                series_id=query.series_id,
                candidate_episodes=tuple(item.episode for item in candidates),
                profile_watch_state=query.profile_watch_state,
                corpus_access_scope=query.corpus_access_scope,
                limit=self._configuration.retrieval_evidence_limit,
            )
        )
        evidence_by_episode: dict = {}
        for match in search_result.matches:
            evidence_by_episode.setdefault(match.episode.episode_id, []).append(match)
        enriched = []
        for candidate in candidates:
            evidence = tuple(evidence_by_episode.get(candidate.episode.episode_id, ()))
            if not evidence:
                continue
            searchable_text = " ".join(
                (
                    candidate.episode_title or "",
                    candidate.synopsis or "",
                    *(item.text for item in evidence),
                )
            )
            if self._contains_excluded_theme(searchable_text, query.excluded_themes):
                continue
            enriched.append(replace(candidate, evidence=evidence))
        enriched.sort(
            key=lambda item: (
                -max(match.score for match in item.evidence),
                item.episode.position,
            )
        )
        return tuple(enriched[: self._configuration.maximum_ranker_candidates])

    def rank_candidates(
        self,
        query: RecommendEpisodesQuery,
        candidates: tuple[RecommendationCandidate, ...],
    ) -> tuple[RankedRecommendationDraft, ...]:
        if not candidates:
            return ()
        return self._ranker.rank(
            RecommendationRankingRequest(
                mood=query.mood,
                characters=query.characters,
                excluded_themes=query.excluded_themes,
                requested_count=query.requested_count,
                candidates=candidates,
            )
        )

    def validate_ranked_candidates(
        self,
        query: RecommendEpisodesQuery,
        candidates: tuple[RecommendationCandidate, ...],
        drafts: tuple[RankedRecommendationDraft, ...],
        *,
        visible_candidate_count: int,
    ) -> RecommendEpisodesResult:
        if len(drafts) > query.requested_count:
            raise ValueError(RecommendationErrorMessages.REQUESTED_COUNT_MUST_BE_VALID)
        if len({item.episode_id for item in drafts}) != len(drafts):
            raise ValueError(RecommendationErrorMessages.RANKER_RESULTS_MUST_BE_UNIQUE)
        candidates_by_id = {item.episode.episode_id: item for item in candidates}
        recommendations = []
        for draft in drafts:
            candidate = candidates_by_id.get(draft.episode_id)
            if candidate is None:
                raise ValueError(
                    RecommendationErrorMessages.RANKER_RESULT_MUST_REFERENCE_CANDIDATE
                )
            if (
                isinstance(draft.score, bool)
                or not math.isfinite(draft.score)
                or draft.score < 0
                or draft.score > 1
            ):
                raise ValueError(
                    RecommendationErrorMessages.RANKER_SCORE_MUST_BE_PROBABILITY
                )
            if not draft.reason or draft.reason.strip() != draft.reason:
                raise ValueError(
                    RecommendationErrorMessages.RANKER_REASON_MUST_BE_TRIMMED
                )
            evidence_by_id = {item.segment_id: item for item in candidate.evidence}
            if (
                not draft.cited_segment_ids
                or len(set(draft.cited_segment_ids)) != len(draft.cited_segment_ids)
                or any(item not in evidence_by_id for item in draft.cited_segment_ids)
            ):
                raise ValueError(
                    RecommendationErrorMessages.RANKER_CITATIONS_MUST_BE_VISIBLE
                )
            recommendations.append(
                EpisodeRecommendation(
                    episode=candidate.episode,
                    episode_title=candidate.episode_title,
                    runtime_seconds=candidate.runtime_seconds,
                    score=draft.score,
                    reason=draft.reason,
                    citations=tuple(
                        evidence_by_id[item] for item in draft.cited_segment_ids
                    ),
                )
            )
        return RecommendEpisodesResult(
            recommendations=tuple(recommendations),
            visible_candidate_count=visible_candidate_count,
        )

    def empty_result(self, visible_candidate_count: int) -> RecommendEpisodesResult:
        return RecommendEpisodesResult((), visible_candidate_count)

    def _validate_query(self, query: RecommendEpisodesQuery) -> None:
        if (
            not query.mood
            or query.mood.strip() != query.mood
            or len(query.mood) > self._configuration.maximum_term_length
        ):
            raise ValueError(RecommendationErrorMessages.MOOD_MUST_BE_TRIMMED)
        if not isinstance(query.watch_preference, WatchPreference):
            raise ValueError(
                RecommendationErrorMessages.WATCH_PREFERENCE_MUST_BE_VALID
            )
        self._validate_terms(
            query.characters,
            self._configuration.maximum_characters,
        )
        self._validate_terms(
            query.excluded_themes,
            self._configuration.maximum_excluded_themes,
        )
        if (
            isinstance(query.requested_count, bool)
            or query.requested_count < 1
            or query.requested_count
            > self._configuration.maximum_requested_count
        ):
            raise ValueError(RecommendationErrorMessages.REQUESTED_COUNT_MUST_BE_VALID)
        if (
            query.maximum_runtime_seconds is not None
            and (
                isinstance(query.maximum_runtime_seconds, bool)
                or query.maximum_runtime_seconds < 1
            )
        ):
            raise ValueError(
                RecommendationErrorMessages.MAXIMUM_RUNTIME_MUST_BE_POSITIVE
            )

    def _validate_terms(self, terms: tuple[str, ...], maximum_count: int) -> None:
        if not isinstance(terms, tuple):
            raise ValueError(RecommendationErrorMessages.TERMS_MUST_BE_IMMUTABLE)
        normalized = []
        for term in terms:
            if (
                not isinstance(term, str)
                or not term
                or term.strip() != term
                or len(term) > self._configuration.maximum_term_length
            ):
                raise ValueError(RecommendationErrorMessages.TERMS_MUST_BE_UNIQUE)
            normalized.append(term.casefold())
        if len(terms) > maximum_count or len(set(normalized)) != len(normalized):
            raise ValueError(RecommendationErrorMessages.TERMS_MUST_BE_UNIQUE)

    @staticmethod
    def _contains_excluded_theme(text: str, themes: tuple[str, ...]) -> bool:
        normalized = text.casefold()
        return any(theme.casefold() in normalized for theme in themes)

    @staticmethod
    def _render_retrieval_query(query: RecommendEpisodesQuery) -> str:
        parts = [f"Mood: {query.mood}"]
        if query.characters:
            parts.append(f"Characters: {', '.join(query.characters)}")
        return "; ".join(parts)
