from uuid import UUID

from cinegraph.adapters.llm.langchain_chat_model_gateway import (
    AnswerSchema,
    LangChainChatModelGateway,
)
from cinegraph.adapters.llm.prompts import build_prompt
from cinegraph.common.prompts import (
    GROUNDED_ANSWER_SYSTEM_PROMPT,
    UNTRUSTED_TRANSCRIPT_EVIDENCE_BOUNDARY,
)
from cinegraph.application.models.grounded_answer import ModelEvidence, ModelRequest
from tests.factories import make_episode_ref


SEGMENT_ID_1 = UUID(int=1)
SEGMENT_ID_2 = UUID(int=2)


class RecordingStructuredInvoker:
    def __init__(self, response: AnswerSchema) -> None:
        self._response = response
        self.invocations: list[dict] = []

    def invoke(self, input: dict, config=None, **kwargs) -> AnswerSchema:
        self.invocations.append(input)
        return self._response


def evidence(
    *,
    segment_id: UUID,
    start_ms: int,
    text: str,
    season_number: int = 1,
    episode_number: int = 1,
) -> ModelEvidence:
    return ModelEvidence(
        segment_id=segment_id,
        episode=make_episode_ref(
            season_number=season_number, episode_number=episode_number
        ),
        start_ms=start_ms,
        end_ms=start_ms + 1_000,
        text=text,
    )


def test_generate_answer_sends_only_question_and_rendered_evidence_to_invoker() -> (
    None
):
    request = ModelRequest(
        question="Why did Luke get stuck?",
        evidence=(
            evidence(
                segment_id=SEGMENT_ID_1,
                start_ms=1_000,
                text="Luke got his head stuck.",
                season_number=1,
                episode_number=1,
            ),
            evidence(
                segment_id=SEGMENT_ID_2,
                start_ms=5_000,
                text="Phil tried to help.",
                season_number=1,
                episode_number=2,
            ),
        ),
    )
    stub = RecordingStructuredInvoker(
        AnswerSchema(answer="Answer.", cited_segment_ids=(SEGMENT_ID_1,))
    )
    gateway = LangChainChatModelGateway(stub)

    gateway.generate_answer(request)

    [sent_input] = stub.invocations
    assert set(sent_input.keys()) == {"question", "evidence"}
    assert sent_input["question"] == "Why did Luke get stuck?"
    rendered = sent_input["evidence"]
    assert "BEGIN_UNTRUSTED_TRANSCRIPT_EVIDENCE" in rendered
    assert "END_UNTRUSTED_TRANSCRIPT_EVIDENCE" in rendered
    assert str(SEGMENT_ID_1) in rendered
    assert str(SEGMENT_ID_2) in rendered
    assert "start_ms=1000" in rendered
    assert "end_ms=2000" in rendered
    assert "start_ms=5000" in rendered
    assert "end_ms=6000" in rendered
    assert "season=1" in rendered
    assert "episode=1" in rendered
    assert "episode=2" in rendered
    assert "Luke got his head stuck." in rendered
    assert "Phil tried to help." in rendered


def test_generate_answer_maps_structured_response_losslessly_to_model_draft() -> None:
    request = ModelRequest(
        question="Q?",
        evidence=(evidence(segment_id=SEGMENT_ID_1, start_ms=0, text="Text."),),
    )
    stub = RecordingStructuredInvoker(
        AnswerSchema(
            answer="The answer.", cited_segment_ids=(SEGMENT_ID_1, SEGMENT_ID_2)
        )
    )
    gateway = LangChainChatModelGateway(stub)

    draft = gateway.generate_answer(request)

    assert draft.answer == "The answer."
    assert draft.cited_segment_ids == (SEGMENT_ID_1, SEGMENT_ID_2)


def test_generate_answer_maps_null_answer_and_empty_citations() -> None:
    request = ModelRequest(
        question="Q?",
        evidence=(evidence(segment_id=SEGMENT_ID_1, start_ms=0, text="Text."),),
    )
    stub = RecordingStructuredInvoker(AnswerSchema(answer=None, cited_segment_ids=()))
    gateway = LangChainChatModelGateway(stub)

    draft = gateway.generate_answer(request)

    assert draft.answer is None
    assert draft.cited_segment_ids == ()


def test_prompt_has_required_variables_and_instruction_boundary() -> None:
    prompt = build_prompt()

    assert set(prompt.input_variables) == {"question", "evidence"}

    rendered = prompt.invoke(
        {"question": "Why?", "evidence": "some transcript evidence"}
    ).to_string()

    assert GROUNDED_ANSWER_SYSTEM_PROMPT in rendered
    assert UNTRUSTED_TRANSCRIPT_EVIDENCE_BOUNDARY in rendered
    assert "Why?" in rendered
    assert "some transcript evidence" in rendered
