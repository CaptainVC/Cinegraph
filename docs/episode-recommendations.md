# Episode recommendations

The recommendation workflow is a bounded LangGraph pipeline:

```text
filter canonical episodes -> retrieve visible evidence -> rank supplied candidates -> validate
```

Corpus entitlement, spoiler visibility, watched preference, maximum runtime, and
exact excluded-theme matches are deterministic filters. The model cannot discover
or add candidates; it may only score the supplied shortlist. Every returned reason
must cite at least one transcript segment already retrieved for that episode, and
the application rejects unknown episodes, duplicate results, invalid scores, and
foreign citation identifiers.

The workflow returns an empty result when no evidence-backed candidate survives.
It does not weaken the request boundary or substitute uncited model knowledge.
