from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.graph.graph_models import GraphClaim, GraphClaimEvidence, GraphEntity


class GraphClaimStore(Protocol):
    def replace_source_version(
        self,
        new_source_version_id: UUID,
        retired_source_version_id: UUID | None,
        entities: tuple[GraphEntity, ...],
        claims: tuple[GraphClaim, ...],
        evidence: tuple[GraphClaimEvidence, ...],
    ) -> None: ...
