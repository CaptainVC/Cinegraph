from cinegraph.application.exceptions.errors import (
    ConversationThreadProfileMismatchError,
    CorpusAccessDeniedError,
)
from cinegraph.application.models.conversation import (
    ConversationalSeriesChatQuery,
    ConversationThreadBinding,
)
from cinegraph.application.models.series_agent_context import SeriesAgentRuntimeContext
from cinegraph.application.models.series_agent_result import SeriesAgentResult
from cinegraph.common.error_messages import ConversationErrorMessages
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.ports.conversation.conversation_thread_binding_repository import (
    ConversationThreadBindingRepository,
)
from cinegraph.ports.conversation.series_conversational_agent import SeriesConversationalAgent
from cinegraph.ports.repository.watch_progress_repository import WatchProgressRepository


class ConversationalSeriesChatService:
    """Authorize and bind a series conversation before invoking LangGraph."""

    def __init__(
        self,
        watch_progress_repository: WatchProgressRepository,
        binding_repository: ConversationThreadBindingRepository,
        agent: SeriesConversationalAgent,
    ) -> None:
        self._watch_progress_repository = watch_progress_repository
        self._binding_repository = binding_repository
        self._agent = agent

    def execute(self, query: ConversationalSeriesChatQuery) -> SeriesAgentResult:
        if (
            not isinstance(query.question, str)
            or not query.question.strip()
            or query.question.strip() != query.question
        ):
            raise InvalidModelError(ConversationErrorMessages.SERIES_QUERY_QUESTION_MUST_BE_BOUNDED)
        if not query.corpus_access_scope.allows_all(query.candidate_episodes):
            raise CorpusAccessDeniedError()
        watch_state = (
            query.profile_watch_state
            if query.profile_watch_state is not None
            else self._watch_progress_repository.get(query.profile_id)
        )
        if watch_state is not None and watch_state.profile_id != query.profile_id:
            raise ConversationThreadProfileMismatchError(query.thread_id)
        series_watch_state = (
            watch_state.series_watch_state_for(query.series_id) if watch_state is not None else None
        )
        binding = ConversationThreadBinding(
            profile_id=query.profile_id,
            watch_state_version=watch_state.version if watch_state is not None else 0,
            permission_scope_revision=query.permission_scope_revision,
            corpus_access_scope=query.corpus_access_scope,
            candidate_episode_ids=tuple(item.episode_id for item in query.candidate_episodes),
            spoiler_mode=watch_state.spoiler_mode if watch_state is not None else SpoilerMode.RELAXED,
            safe_through_episode_id=(
                series_watch_state.sequential_safe_boundary.episode_id
                if series_watch_state is not None
                and series_watch_state.sequential_safe_boundary is not None
                else None
            ),
        )
        self._binding_repository.bind_or_validate(query.thread_id, binding)
        context = SeriesAgentRuntimeContext(
            series_id=query.series_id,
            candidate_episodes=query.candidate_episodes,
            profile_watch_state=watch_state,
            corpus_access_scope=query.corpus_access_scope,
        )
        return self._agent.invoke(query.question, context, query.thread_id)
