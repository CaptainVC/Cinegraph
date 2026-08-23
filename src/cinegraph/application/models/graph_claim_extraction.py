from dataclasses import dataclass

from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.transcript.transcript_retrieval_chunk import TranscriptRetrievalChunk
from cinegraph.ports.graph.graph_claim_models import (
    ExtractedEntityReference,
    ExtractedGraphClaim,
)

__all__ = [
    "ExtractedEntityReference",
    "ExtractedGraphClaim",
    "ExtractAndReplaceGraphClaimsCommand",
    "ExtractAndReplaceGraphClaimsResult",
]


@dataclass(frozen=True, slots=True)
class ExtractAndReplaceGraphClaimsCommand:
    source_version: SourceVersion
    chunks: tuple[TranscriptRetrievalChunk, ...]


@dataclass(frozen=True, slots=True)
class ExtractAndReplaceGraphClaimsResult:
    input_chunk_count: int
    candidate_count: int
    entity_count: int
    claim_count: int
    evidence_count: int
