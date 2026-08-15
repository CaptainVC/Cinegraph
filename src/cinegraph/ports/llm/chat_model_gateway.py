from typing import Protocol

from cinegraph.application.models.grounded_answer import ModelDraft, ModelRequest


class ChatModelGateway(Protocol):
    # Generate a structured answer draft grounded in the supplied model evidence.
    def generate_answer(self, request: ModelRequest) -> ModelDraft: ...
