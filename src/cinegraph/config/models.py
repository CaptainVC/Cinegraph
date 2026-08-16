from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    main_model: str
    rag_answer_model: str
    recommendation_model: str
    speaker_review_model: str
    speaker_adjudication_model: str
    speaker_final_review_model: str
    speaker_review_reasoning_effort: str
    speaker_adjudication_reasoning_effort: str
    speaker_final_review_reasoning_effort: str


DEFAULT_MODEL_CONFIGURATION = ModelConfiguration(
    main_model="gpt-5.6-terra",
    rag_answer_model="gpt-4.1-mini",
    recommendation_model="gpt-4.1-mini",
    speaker_review_model="gpt-5.6-luna",
    speaker_adjudication_model="gpt-5.6-terra",
    speaker_final_review_model="gpt-5.6-sol",
    speaker_review_reasoning_effort="low",
    speaker_adjudication_reasoning_effort="medium",
    speaker_final_review_reasoning_effort="high",
)
