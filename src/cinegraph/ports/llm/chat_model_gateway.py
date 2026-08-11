from typing import Protocol

from cinegraph.application.models.grounded_answer import ModelDraft, ModelRequest


class ChatModelGateway(Protocol):
    # Processes the supplied generate answer values.
    def generate_answer(self, request: ModelRequest) -> ModelDraft: ...
