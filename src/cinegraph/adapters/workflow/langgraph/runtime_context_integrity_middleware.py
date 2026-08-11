from collections.abc import Mapping
from typing import Any
from uuid import UUID

from langchain.agents.middleware import AgentMiddleware

from cinegraph.application.exceptions.errors import AgentRuntimeContextInvalidError
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState


class RuntimeContextIntegrityMiddleware(AgentMiddleware):
    # Validate invocation context before the agent can call any model or tool.
    def before_agent(self, state: Any, runtime: Any) -> None:
        del state
        context = getattr(runtime, "context", None)
        if not isinstance(context, Mapping):
            raise AgentRuntimeContextInvalidError()
        if not isinstance(context.get("episode"), EpisodeRef):
            raise AgentRuntimeContextInvalidError()
        if not isinstance(context.get("summary_source_document_id"), UUID):
            raise AgentRuntimeContextInvalidError()
        profile_watch_state = context.get("profile_watch_state")
        if profile_watch_state is not None and not isinstance(
            profile_watch_state, ProfileWatchState
        ):
            raise AgentRuntimeContextInvalidError()