# Series agent runtime

Phase 31 provides an authorization-safe series research runtime. The application
binds a thread to profile identity, watch-state version, and the exact corpus
scope before invoking the agent. The agent receives a frozen invocation context:
series ID, immutable entitled candidate episodes, current profile watch state,
and corpus access scope. None of these values are model arguments.

The model can select two read-only tools:

- `grounded_transcript_answer` accepts one bounded trimmed question and calls the
  existing hybrid grounded-answer workflow with a fixed application retrieval
  limit. It returns an answer/refusal and segment/timing IDs only.
- `authorized_graph_relationships` accepts bounded semantic seed and predicate
  lists and calls `GraphRagQueryService` with fixed hops, claim, and evidence
  limits. It returns subject/predicate/object, polarity, score, and stable claim
  and evidence IDs. Conflicting polarities remain distinct.

The adapter uses a private structured response schema: `answer` plus explicit
`citation_ids`. A non-refusal must select one or more IDs returned by a tool in
the current turn. Duplicate, malformed, unknown, empty, or invented IDs produce
a typed safe refusal. A refusal has no citations. Prior checkpoint messages are
available for continuity but never contribute to the current citation allowlist.
Tool projections contain no transcript text, prompts, runtime context, or raw
provider objects.

Central limits cover question and argument sizes, entitled candidate count,
retrieval/GraphRAG caps, model and tool calls, retries, selector width, and
structured citation count. Tool and provider errors are sanitized and bounded;
empty evidence is a normal refusal. Prompt injection guidance explicitly treats
user, transcript, and GraphRAG content as untrusted data, but structural context
and application validation are the authorization boundary.

Model routing defaults to Terra for synthesis and Luna for tool selection and
cost-sensitive grounded work. No API client or persistent checkpointer is
constructed by unrelated bootstrap paths; tests inject deterministic models and
`InMemorySaver` where needed.

Phase 32 added HTTP/SSE exposure, and Phase 34 made job/event state durable in SQL.
Phase 34 also accounts nested synthesis, selector, and grounded-answer model responses
through a context-local LangChain callback ledger; configured call, token, estimated-
cost, retry, and cooperative deadline limits fail to stable private terminal codes.
Structured telemetry contains aggregate usage and lifecycle metadata only. The
bounded dispatcher and LangGraph checkpoint remain process-local until deployment-
phase worker recovery.
