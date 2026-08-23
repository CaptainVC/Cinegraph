from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from cinegraph.application.models.graph_rag import GraphRagQuery
from cinegraph.application.models.series_agent_context import SeriesAgentRuntimeContext
from cinegraph.application.service.graph_rag_service import GraphRagQueryService
from cinegraph.common.error_messages import SeriesAgentErrorMessages
from cinegraph.config.series_agent import (
    DEFAULT_SERIES_AGENT_CONFIGURATION,
    SERIES_GRAPH_TOOL_DESCRIPTION,
    SERIES_GRAPH_TOOL_NAME,
    SeriesAgentConfiguration,
)


def build_series_graph_rag_tool(
    service: GraphRagQueryService,
    configuration: SeriesAgentConfiguration = DEFAULT_SERIES_AGENT_CONFIGURATION,
) -> BaseTool:
    @tool(SERIES_GRAPH_TOOL_NAME, description=SERIES_GRAPH_TOOL_DESCRIPTION)
    def authorized_graph_relationships(
        seed_terms: list[str],
        runtime: ToolRuntime[SeriesAgentRuntimeContext, dict[str, object]],
        predicates: list[str] | None = None,
    ) -> dict[str, object]:
        cfg = configuration
        if (
            not isinstance(seed_terms, list)
            or not seed_terms
            or len(seed_terms) > cfg.graph_seed_limit
            or any(
                not isinstance(item, str)
                or not item.strip()
                or item.strip() != item
                or len(item) > cfg.graph_seed_max_length
                for item in seed_terms
            )
        ):
            raise ValueError(SeriesAgentErrorMessages.GRAPH_SEEDS_INVALID)
        predicate_values = [] if predicates is None else predicates
        if (
            not isinstance(predicate_values, list)
            or len(predicate_values) > cfg.graph_predicate_limit
            or any(
                not isinstance(item, str)
                or not item.strip()
                or item.strip() != item
                or len(item) > cfg.graph_predicate_max_length
                for item in predicate_values
            )
        ):
            raise ValueError(SeriesAgentErrorMessages.GRAPH_PREDICATES_INVALID)
        if runtime is None:
            raise ValueError(SeriesAgentErrorMessages.GRAPH_CONTEXT_REQUIRED)
        context = runtime.context
        result = service.execute(
            GraphRagQuery(
                series_id=context.series_id,
                seed_terms=tuple(seed_terms),
                predicates=tuple(predicate_values),
                candidate_episodes=context.candidate_episodes,
                profile_watch_state=context.profile_watch_state,
                corpus_access_scope=context.corpus_access_scope,
                hops=cfg.graph_hops,
                claim_limit=cfg.graph_claim_limit,
                evidence_per_claim=cfg.graph_evidence_per_claim,
            )
        )
        claims = []
        for claim in result.claims:
            claims.append(
                {
                    "claim_id": str(claim.claim_id),
                    "series_id": str(claim.series_id),
                    "subject": claim.subject.display_name,
                    "predicate": claim.predicate,
                    "object": claim.object.display_name,
                    "polarity": claim.polarity.value,
                    "score": claim.score,
                    "evidence": [
                        {
                            "evidence_id": str(evidence.evidence_id),
                            "episode_id": str(evidence.episode.episode_id),
                            "season_number": evidence.episode.position.season_number,
                            "episode_number": evidence.episode.position.episode_number,
                            "start_ms": evidence.start_ms,
                            "end_ms": evidence.end_ms,
                        }
                        for evidence in claim.evidence
                    ],
                }
            )
        return {"claims": claims}

    return authorized_graph_relationships


build_authorized_graph_rag_tool = build_series_graph_rag_tool
