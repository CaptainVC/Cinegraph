from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from cinegraph.adapters.evidence import (
    AuthorizedAgentEvidenceReader,
    build_agent_evidence_request,
)
from cinegraph.application.models.agent_job import AgentJob, AgentJobStatus
from cinegraph.application.models.series_agent_result import SeriesAgentCitation, SeriesAgentResult
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config.graph_claims import GRAPH_CLAIM_EXTRACTION_REVISION
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    CorpusAccessMode,
    GraphClaimPolarity,
    GraphEntityKind,
    Language,
    RightsStatus,
)
from cinegraph.domain.models.access import CorpusAccessScope, CorpusSeasonAccess
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef
from cinegraph.ports.agent_jobs.agent_evidence_reader import AgentEvidenceNotFoundError
from cinegraph.ports.retrieval.vector_index import RetrievedSegment

SERIES_ID = UUID(int=101)
PROFILE_ID = UUID(int=102)
EPISODE = EpisodeRef(SERIES_ID, UUID(int=103), UUID(int=104), EpisodePosition(1, 1))
SCOPE = CorpusAccessScope(
    CorpusAccessMode.GUEST,
    "scope-v1",
    frozenset({CorpusSeasonAccess(SERIES_ID, 1)}),
)


class RecordingIndex:
    def __init__(self, segments: tuple[RetrievedSegment, ...]) -> None:
        self.segments = {item.segment_id: item for item in segments}
        self.calls = []

    def retrieve_by_ids(self, segment_ids, scope):
        self.calls.append((segment_ids, scope))
        return tuple(self.segments[item] for item in segment_ids if item in self.segments)


class RecordingGraphService:
    def __init__(self, claims=()) -> None:
        self.claims = tuple(claims)
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return SimpleNamespace(claims=self.claims)


def _segment(
    segment_id: UUID,
    source_version_id: UUID,
    *,
    episode: EpisodeRef = EPISODE,
    start_ms: int = 1_000,
    end_ms: int = 2_000,
    text: str = "Authorized synthetic evidence.",
) -> RetrievedSegment:
    return RetrievedSegment(
        segment_id=segment_id,
        source_version_id=source_version_id,
        episode=episode,
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
        score=0.0,
        member_segment_ids=(segment_id,),
        index_revision=TRANSCRIPT_INDEX_REVISION,
        ordinal=0,
    )


def _job(citation: SeriesAgentCitation, tool_name: str) -> AgentJob:
    now = datetime(2026, 8, 27, tzinfo=UTC)
    return AgentJob(
        job_id=UUID(int=201),
        owner_profile_id=PROFILE_ID,
        thread_id=UUID(int=202),
        series_id=SERIES_ID,
        question="A bounded question",
        candidate_episodes=(EPISODE,),
        corpus_access_scope=SCOPE,
        permission_scope_revision=SCOPE.revision,
        idempotency_key=str(UUID(int=203)),
        request_fingerprint="a" * 64,
        created_at=now,
        status=AgentJobStatus.SUCCEEDED,
        started_at=now,
        finished_at=now,
        result=SeriesAgentResult(
            "A grounded answer.",
            False,
            (citation,),
            (tool_name,),
        ),
    )


def test_transcript_hydration_recompiles_scope_and_matches_locator_exactly() -> None:
    segment_id, source_id = UUID(int=301), UUID(int=302)
    citation = SeriesAgentCitation(
        "transcript", EPISODE, 1_000, 2_000, segment_id=segment_id
    )
    index = RecordingIndex((_segment(segment_id, source_id),))
    graph = RecordingGraphService()
    reader = AuthorizedAgentEvidenceReader(index, graph)

    result = reader.read(
        build_agent_evidence_request(
            _job(citation, "grounded_transcript_answer"), (segment_id,)
        ),
        SCOPE,
    )

    assert result.excerpts[0].text == "Authorized synthetic evidence."
    assert result.excerpts[0].citation_id == segment_id
    assert index.calls[0][0] == (segment_id,)
    assert tuple(item.episode for item in index.calls[0][1].episode_scopes) == (EPISODE,)
    assert graph.calls == []


def test_evidence_request_builder_rejects_jobs_without_a_result() -> None:
    citation = SeriesAgentCitation(
        "transcript", EPISODE, 1_000, 2_000, segment_id=UUID(int=305)
    )
    running_job = replace(
        _job(citation, "grounded_transcript_answer"),
        status=AgentJobStatus.RUNNING,
        finished_at=None,
        result=None,
    )

    with pytest.raises(AgentEvidenceNotFoundError):
        build_agent_evidence_request(running_job, (citation.citation_id,))


@pytest.mark.parametrize(
    "failure", ["unknown", "duplicate", "revision", "timing", "episode", "long_text"]
)
def test_transcript_hydration_fails_closed_for_stale_or_mismatched_evidence(failure) -> None:
    segment_id, source_id = UUID(int=311), UUID(int=312)
    citation = SeriesAgentCitation(
        "transcript", EPISODE, 1_000, 2_000, segment_id=segment_id
    )
    segment = _segment(segment_id, source_id)
    citation_ids = (segment_id,)
    scope = SCOPE
    if failure == "unknown":
        citation_ids = (UUID(int=999),)
    elif failure == "duplicate":
        citation_ids = (segment_id, segment_id)
    elif failure == "revision":
        scope = replace(SCOPE, revision="scope-v2")
    elif failure == "timing":
        segment = _segment(segment_id, source_id, start_ms=1_001)
    elif failure == "episode":
        other = EpisodeRef(SERIES_ID, UUID(int=313), UUID(int=314), EpisodePosition(1, 2))
        segment = _segment(segment_id, source_id, episode=other)
    elif failure == "long_text":
        segment = _segment(segment_id, source_id, text="x" * 4_001)

    reader = AuthorizedAgentEvidenceReader(RecordingIndex((segment,)), RecordingGraphService())
    with pytest.raises(AgentEvidenceNotFoundError):
        reader.read(
            build_agent_evidence_request(
                _job(citation, "grounded_transcript_answer"), citation_ids
            ),
            scope,
        )


def _graph_fixture():
    source_id, chunk_id = UUID(int=401), UUID(int=402)
    subject_id = IdentifierGenerator.graph_entity_id(
        SERIES_ID, GraphEntityKind.CHARACTER, "claire"
    )
    object_id = IdentifierGenerator.graph_entity_id(
        SERIES_ID, GraphEntityKind.CHARACTER, "phil"
    )
    claim_id = IdentifierGenerator.graph_claim_id(
        GRAPH_CLAIM_EXTRACTION_REVISION,
        SERIES_ID,
        subject_id,
        "married_to",
        object_id,
        GraphClaimPolarity.ASSERTED,
    )
    evidence_id = IdentifierGenerator.graph_evidence_id(claim_id, source_id, chunk_id)
    citation = SeriesAgentCitation(
        "graph",
        EPISODE,
        1_000,
        2_000,
        claim_id=claim_id,
        evidence_id=evidence_id,
        source_version_id=source_id,
        transcript_chunk_id=chunk_id,
        subject_entity_id=subject_id,
        subject_kind=GraphEntityKind.CHARACTER,
        subject_display_name="Claire",
        predicate="married_to",
        object_entity_id=object_id,
        object_kind=GraphEntityKind.CHARACTER,
        object_display_name="Phil",
        polarity=GraphClaimPolarity.ASSERTED,
        hop_distance=1,
        score=0.95,
    )
    evidence = SimpleNamespace(
        evidence_id=evidence_id,
        source_version_id=source_id,
        transcript_chunk_id=chunk_id,
    )
    claim = SimpleNamespace(claim_id=claim_id, evidence=(evidence,))
    return citation, claim, _segment(chunk_id, source_id)


def test_graph_hydration_revalidates_claim_evidence_and_source_before_text_read() -> None:
    citation, claim, segment = _graph_fixture()
    index = RecordingIndex((segment,))
    graph = RecordingGraphService((claim,))
    reader = AuthorizedAgentEvidenceReader(index, graph)

    result = reader.read(
        build_agent_evidence_request(
            _job(citation, "authorized_graph_relationships"),
            (citation.evidence_id,),
        ),
        SCOPE,
    )

    assert result.excerpts[0].citation_id == citation.evidence_id
    assert graph.calls[0].candidate_episodes == (EPISODE,)
    assert graph.calls[0].corpus_access_scope == SCOPE
    assert index.calls[0][0] == (citation.transcript_chunk_id,)


def test_graph_hydration_batches_authorization_requeries_within_graph_limits() -> None:
    citation, claim, _segment_template = _graph_fixture()
    citations_list = []
    for index in range(5):
        chunk_id = UUID(int=430 + index)
        citations_list.append(
            replace(
                citation,
                evidence_id=IdentifierGenerator.graph_evidence_id(
                    citation.claim_id, citation.source_version_id, chunk_id
                ),
                transcript_chunk_id=chunk_id,
                start_ms=1_000 + index,
                end_ms=2_000 + index,
            )
        )
    citations = tuple(citations_list)
    evidence = tuple(
        SimpleNamespace(
            evidence_id=item.evidence_id,
            source_version_id=item.source_version_id,
            transcript_chunk_id=item.transcript_chunk_id,
        )
        for item in citations
    )
    graph = RecordingGraphService(
        (SimpleNamespace(claim_id=claim.claim_id, evidence=evidence),)
    )
    segments = tuple(
        _segment(
            item.transcript_chunk_id,
            item.source_version_id,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
        )
        for item in citations
    )
    job = replace(
        _job(citation, "authorized_graph_relationships"),
        result=SeriesAgentResult(
            "A grounded answer.",
            False,
            citations,
            ("authorized_graph_relationships",),
        ),
    )
    reader = AuthorizedAgentEvidenceReader(RecordingIndex(segments), graph)

    result = reader.read(
        build_agent_evidence_request(
            job, tuple(item.citation_id for item in citations)
        ),
        SCOPE,
    )

    assert len(result.excerpts) == 5
    assert len(graph.calls) == 2
    assert all(len(call.seed_terms) <= 8 for call in graph.calls)


@pytest.mark.parametrize("failure", ["retired", "source", "legacy"])
def test_graph_hydration_fails_closed_when_current_evidence_cannot_be_proven(failure) -> None:
    citation, claim, segment = _graph_fixture()
    claims = (claim,)
    if failure == "retired":
        claims = ()
    elif failure == "source":
        segment = _segment(segment.segment_id, UUID(int=999))
    elif failure == "legacy":
        citation = SeriesAgentCitation(
            "graph",
            EPISODE,
            1_000,
            2_000,
            claim_id=citation.claim_id,
            evidence_id=citation.evidence_id,
        )

    reader = AuthorizedAgentEvidenceReader(
        RecordingIndex((segment,)), RecordingGraphService(claims)
    )
    with pytest.raises(AgentEvidenceNotFoundError):
        reader.read(
            build_agent_evidence_request(
                _job(citation, "authorized_graph_relationships"),
                (citation.evidence_id,),
            ),
            SCOPE,
        )
