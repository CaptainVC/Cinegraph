# Graph claims

The graph extraction boundary accepts reviewed, active, rights-allowed transcript
chunks and emits typed entity-to-entity claims. The extractor is vendor-neutral so
the future LangGraph workflow can use a low-cost structured model while keeping
governance and persistence in the application.

Entities are series-scoped and normalized with Unicode NFKC, whitespace collapse,
and casefolding. Claims are merged by stable semantic identity; aliases are unioned
and evidence confidence is retained per source chunk. No ungrounded claim can be
stored because every candidate must cite an input chunk from the same replacement.

`graph_claim_extraction` is a durable ingestion-job kind. Changing the centralized
extraction revision requires re-extracting the complete governed corpus. Phase 30
will add authorized traversal and conflict ranking; this phase intentionally does
not expose graph reads.
