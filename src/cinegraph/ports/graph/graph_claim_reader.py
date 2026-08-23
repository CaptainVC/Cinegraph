from typing import Protocol

from cinegraph.domain.retrieval.retrieval_scope import RetrievalScope
from cinegraph.ports.graph.graph_claim_read_models import GraphRagReadClaim


class GraphClaimReader(Protocol):
    def read(
        self,
        *,
        scope: RetrievalScope,
        seed_terms: tuple[str, ...],
        predicates: tuple[str, ...],
        hops: int,
        claim_limit: int,
        evidence_per_claim: int,
        max_frontier: int,
    ) -> tuple[GraphRagReadClaim, ...]: ...
