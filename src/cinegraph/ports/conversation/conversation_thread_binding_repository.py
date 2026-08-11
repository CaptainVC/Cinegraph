from typing import Protocol
from uuid import UUID

from cinegraph.application.models.conversation import ConversationThreadBinding


class ConversationThreadBindingRepository(Protocol):

    # Atomically bind a new thread or validate its existing immutable binding.
    def bind_or_validate(
        self,
        thread_id: UUID,
        binding: ConversationThreadBinding,
    ) -> None: ...