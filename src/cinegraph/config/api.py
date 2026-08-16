from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApiConfiguration:
    title: str
    version: str
    api_prefix: str
    catalogue_manifest_filename: str
    minimum_question_length: int
    maximum_question_length: int
    default_retrieval_limit: int
    maximum_retrieval_limit: int


DEFAULT_API_CONFIGURATION = ApiConfiguration(
    title="Cinegraph API",
    version="1.0.0",
    api_prefix="/api/v1",
    catalogue_manifest_filename="catalogue.json",
    minimum_question_length=2,
    maximum_question_length=2_000,
    default_retrieval_limit=8,
    maximum_retrieval_limit=20,
)
