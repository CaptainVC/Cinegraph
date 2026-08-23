# ADR-0005: Revisioned transcript chunks and source replacement

## Status

Accepted

## Decision

Transcript evidence is persisted as deterministic, speaker-attributed chunks. A
chunk ID is derived from the chunking revision, source version, episode, and ordered
member cue IDs. Index writes replace a source version by upserting all new points
before deleting points belonging to the retired parent. Qdrant payloads and retrieval
mapping independently enforce active/approved/allowed governance and the exact current
index revision. Hybrid search overfetches bounded candidates and removes redundant
same-episode chunks using member-cue overlap.

## Consequences

Changing chunking, embedding, or payload semantics requires a new index revision and
a full corpus reindex. Legacy cue points remain physically present until cleanup but
are excluded by the revision filter. A replacement with zero chunks still retires its
parent safely. The synthetic gate uses an in-memory Qdrant collection and a hash-only
encoder so CI never downloads models or accesses private corpus data.
