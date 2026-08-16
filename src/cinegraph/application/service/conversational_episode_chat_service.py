from cinegraph.application.exceptions.errors import (
    ConversationThreadProfileMismatchError,
    CorpusAccessDeniedError,
)
from cinegraph.application.models.agent_context import AgentRuntimeContext
from cinegraph.application.models.conversation import (
    ConversationalEpisodeChatQuery,
    ConversationThreadBinding,
)
from cinegraph.ports.conversation.conversation_thread_binding_repository import (
    ConversationThreadBindingRepository,
)
from cinegraph.ports.conversation.conversational_agent import ConversationalAgent
from cinegraph.ports.repository.watch_progress_repository import WatchProgressRepository


class ConversationalEpisodeChatService:

    # Store ports for fresh watch-state loading, binding validation, and invocation.
    def __init__(
        self,
        watch_progress_repository: WatchProgressRepository,
        binding_repository: ConversationThreadBindingRepository,
        agent: ConversationalAgent,
    ) -> None:
        self._watch_progress_repository = watch_progress_repository
        self._binding_repository = binding_repository
        self._agent = agent

    # Reload state, validate the thread boundary, and invoke the episode agent.
    def execute(self, query: ConversationalEpisodeChatQuery) -> dict[str, object]:
        # Stop before repository or model access when the requested corpus is not entitled.
        if not query.corpus_access_scope.allows_episode(query.episode):
            raise CorpusAccessDeniedError()

        # Reload the caller's current watch state before deriving the thread binding.
        watch_state = self._watch_progress_repository.get(query.profile_id)
        # Reject a repository result that crosses the requested profile boundary.
        if watch_state is not None and watch_state.profile_id != query.profile_id:
            raise ConversationThreadProfileMismatchError(query.thread_id)

        # Construct the immutable binding and atomically validate or establish it.
        binding = ConversationThreadBinding(
            profile_id=query.profile_id,
            watch_state_version=watch_state.version if watch_state is not None else 0,
            permission_scope_revision=query.permission_scope_revision,
            corpus_access_scope=query.corpus_access_scope,
        )
        self._binding_repository.bind_or_validate(query.thread_id, binding)

        # Build runtime context for the agent without adding it to the message input.
        context: AgentRuntimeContext = {
            "episode": query.episode,
            "summary_source_document_id": query.summary_source_document_id,
            "profile_watch_state": watch_state,
            "corpus_access_scope": query.corpus_access_scope,
        }
        # Invoke the agent with the question, runtime context, and thread identity.
        return self._agent.invoke(query.question, context, query.thread_id)
