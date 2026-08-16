from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import ConversationErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


@dataclass(frozen=True, slots=True)
class ConversationThreadBinding:
    profile_id: UUID
    watch_state_version: int
    permission_scope_revision: str
    corpus_access_scope: CorpusAccessScope

    # Enforce the immutable thread binding's version and scope invariants.
    def __post_init__(self) -> None:
        if self.watch_state_version < 0:
            raise InvalidModelError(
                ConversationErrorMessages.BINDING_WATCH_STATE_VERSION_MUST_BE_NON_NEGATIVE
            )
        if (
            not self.permission_scope_revision
            or self.permission_scope_revision.strip() != self.permission_scope_revision
        ):
            raise InvalidModelError(
                ConversationErrorMessages.BINDING_PERMISSION_SCOPE_REVISION_MUST_BE_NONEMPTY
            )
        if self.permission_scope_revision != self.corpus_access_scope.revision:
            raise InvalidModelError(
                ConversationErrorMessages.BINDING_PERMISSION_SCOPE_REVISION_MUST_MATCH_ACCESS_SCOPE
            )


@dataclass(frozen=True, slots=True)
class ConversationalEpisodeChatQuery:
    thread_id: UUID
    profile_id: UUID
    permission_scope_revision: str
    question: str
    episode: EpisodeRef
    summary_source_document_id: UUID
    corpus_access_scope: CorpusAccessScope
