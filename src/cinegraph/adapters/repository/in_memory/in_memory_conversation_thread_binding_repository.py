from threading import Lock
from uuid import UUID

from cinegraph.application.exceptions.errors import (
    ConversationThreadProfileMismatchError,
    ConversationThreadScopeMismatchError,
    ConversationThreadWatchStateMismatchError,
)
from cinegraph.application.models.conversation import ConversationThreadBinding


class InMemoryConversationThreadBindingRepository:

    # Initialize isolated bindings protected by an instance-local lock.
    def __init__(self) -> None:
        self._bindings: dict[UUID, ConversationThreadBinding] = {}
        self._lock = Lock()

    # Atomically bind an unbound thread or reject any changed binding field.
    def bind_or_validate(
        self,
        thread_id: UUID,
        binding: ConversationThreadBinding,
    ) -> None:
        with self._lock:
            existing_binding = self._bindings.get(thread_id)
            # Bind a new thread to its first immutable binding.
            if existing_binding is None:
                self._bindings[thread_id] = binding
                return
            # Reject attempts to reuse the thread for another profile.
            if existing_binding.profile_id != binding.profile_id:
                raise ConversationThreadProfileMismatchError(thread_id)
            # Reject attempts to reuse the thread with another watch-state version.
            if existing_binding.watch_state_version != binding.watch_state_version:
                raise ConversationThreadWatchStateMismatchError(thread_id)
            # Reject attempts to reuse the thread with another permission scope.
            if (
                existing_binding.permission_scope_revision
                != binding.permission_scope_revision
                or existing_binding.corpus_access_scope != binding.corpus_access_scope
            ):
                raise ConversationThreadScopeMismatchError(thread_id)
            # Accept the existing binding when every field matches.
