from typing import Protocol

from cinegraph.domain.models.transcript.transcript_retrieval_chunk import TranscriptRetrievalChunk
from cinegraph.ports.graph.graph_claim_models import ExtractedGraphClaim


class GraphClaimExtractor(Protocol):
    def extract(self, chunks: tuple[TranscriptRetrievalChunk, ...]) -> tuple[ExtractedGraphClaim, ...]: ...
