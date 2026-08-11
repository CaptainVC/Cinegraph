from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from cinegraph.adapters.llm.prompts import build_prompt
from cinegraph.application.models.grounded_answer import (
    ModelDraft,
    ModelEvidence,
    ModelRequest,
)


class AnswerSchema(BaseModel):
    answer: str | None
    cited_segment_ids: tuple[UUID, ...]


# Render one evidence item with its citation metadata and untrusted-text markers.
def _render_evidence(evidence: ModelEvidence) -> str:
    return (
        "BEGIN_UNTRUSTED_TRANSCRIPT_EVIDENCE\n"
        f"segment_id={evidence.segment_id} "
        f"season={evidence.episode.position.season_number} "
        f"episode={evidence.episode.position.episode_number} "
        f"start_ms={evidence.start_ms} end_ms={evidence.end_ms}\n"
        f"{evidence.text}\n"
        "END_UNTRUSTED_TRANSCRIPT_EVIDENCE"
    )


# Render all request evidence as separately delimited sections for the prompt.
def _render_all_evidence(request: ModelRequest) -> str:
    # Render each evidence item independently before joining the prompt sections.
    return "\n\n".join(_render_evidence(evidence) for evidence in request.evidence)


class LangChainChatModelGateway:
    # Store the structured runnable used to generate validated model drafts.
    def __init__(self, structured_runnable: Runnable) -> None:
        self._structured_runnable = structured_runnable

    @classmethod
    # Build a gateway that adds structured AnswerSchema output to the chat model.
    def from_chat_model(cls, model: BaseChatModel) -> "LangChainChatModelGateway":
        return cls(build_prompt() | model.with_structured_output(AnswerSchema))

    # Invoke the model with the question and rendered evidence, then map its output.
    def generate_answer(self, request: ModelRequest) -> ModelDraft:
        # Invoke the structured LangChain runnable with the question and rendered evidence.
        result = self._structured_runnable.invoke(
            {
                "question": request.question,
                "evidence": _render_all_evidence(request),
            }
        )
        return ModelDraft(
            answer=result.answer,
            cited_segment_ids=result.cited_segment_ids,
        )
