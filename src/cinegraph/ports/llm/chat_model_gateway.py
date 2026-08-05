from typing import Protocol

from cinegraph.application.models.grounded_answer import ModelDraft, ModelRequest


class ChatModelGateway(Protocol):
    def generate_answer(self, request: ModelRequest) -> ModelDraft: ...
