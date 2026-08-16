from cinegraph.application.models.search_visible_hybrid_segments import (
    SearchVisibleHybridSegmentsQuery,
    SearchVisibleHybridSegmentsResult,
)
from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.retrieval import RetrievalScope, RetrievalScopeCompiler
from cinegraph.ports.retrieval import VectorEncoder, VectorIndex


class SearchVisibleHybridSegmentsService:
    # Store the policy compiler and retrieval dependencies used by the search use case.
    def __init__(
        self,
        scope_compiler: RetrievalScopeCompiler,
        encoder: VectorEncoder,
        vector_index: VectorIndex,
    ) -> None:
        self._scope_compiler = scope_compiler
        self._encoder = encoder
        self._vector_index = vector_index

    # Compile entitlement and spoiler policy before encoding or querying evidence.
    def execute(
        self,
        query: SearchVisibleHybridSegmentsQuery,
    ) -> SearchVisibleHybridSegmentsResult:
        self._validate_query(query)
        scope = self._scope_compiler.compile(
            series_id=query.series_id,
            candidate_episodes=query.candidate_episodes,
            watch_state=query.profile_watch_state,
            corpus_access_scope=query.corpus_access_scope,
        )
        if not scope.episode_scopes:
            return SearchVisibleHybridSegmentsResult(
                matches=(),
                visible_episode_count=0,
            )

        query_vector = self._encoder.encode_query(query.query)
        matches = self._vector_index.search_hybrid(
            query=query_vector,
            scope=scope,
            limit=query.limit,
        )
        self._validate_results(matches, scope, query.limit)
        return SearchVisibleHybridSegmentsResult(
            matches=matches,
            visible_episode_count=len(scope.episode_scopes),
        )

    @staticmethod
    def _validate_query(query: SearchVisibleHybridSegmentsQuery) -> None:
        if not isinstance(query.query, str) or not query.query or query.query.strip() != query.query:
            raise ValueError(
                RetrievalErrorMessages.SEARCH_QUERY_MUST_BE_TRIMMED_NONEMPTY
            )
        if isinstance(query.limit, bool) or not isinstance(query.limit, int) or query.limit < 1:
            raise ValueError(RetrievalErrorMessages.SEARCH_LIMIT_MUST_BE_POSITIVE)
        if not isinstance(query.candidate_episodes, tuple):
            raise ValueError(
                RetrievalErrorMessages.CANDIDATE_EPISODES_MUST_BE_IMMUTABLE
            )
        episode_ids = {
            episode.episode_id for episode in query.candidate_episodes
        }
        if len(episode_ids) != len(query.candidate_episodes):
            raise ValueError(
                RetrievalErrorMessages.CANDIDATE_EPISODE_IDS_MUST_BE_UNIQUE
            )

    @staticmethod
    def _validate_results(
        matches: tuple,
        scope: RetrievalScope,
        limit: int,
    ) -> None:
        if len(matches) > limit:
            raise InvalidModelError(
                RetrievalErrorMessages.VECTOR_INDEX_RESULT_COUNT_MUST_NOT_EXCEED_LIMIT
            )
        segment_ids = {match.segment_id for match in matches}
        if len(segment_ids) != len(matches):
            raise InvalidModelError(
                RetrievalErrorMessages.VECTOR_INDEX_RESULT_IDS_MUST_BE_UNIQUE
            )
        visibility_by_episode = {
            item.episode.episode_id: item for item in scope.episode_scopes
        }
        for match in matches:
            visibility = visibility_by_episode.get(match.episode.episode_id)
            if (
                visibility is None
                or visibility.episode != match.episode
                or (
                    visibility.safe_until_ms is not None
                    and match.end_ms > visibility.safe_until_ms
                )
            ):
                raise InvalidModelError(
                    RetrievalErrorMessages.VECTOR_INDEX_RESULT_MUST_MATCH_SCOPE
                )
