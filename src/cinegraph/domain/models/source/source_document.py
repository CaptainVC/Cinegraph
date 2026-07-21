from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import SourceErrorMessages
from cinegraph.domain.enums.enum import SourceKind
from cinegraph.domain.exceptions.errors import InvalidModelError

@dataclass(frozen=True, slots=True)
class SourceDocument:
    source_document_id: UUID
    title: str
    kind: SourceKind
    origin: str

    def __post_init__(self) -> None:
        if not self.title or self.title.strip() != self.title:
            raise InvalidModelError(
                SourceErrorMessages.SOURCE_DOCUMENT_TITLE_MUST_BE_TRIMMED
            )

        if not self.origin or self.origin.strip() != self.origin:
            raise InvalidModelError(
                SourceErrorMessages.SOURCE_DOCUMENT_ORIGIN_MUST_BE_TRIMMED
            )
