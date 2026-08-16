# Retrieval evaluation gate

Private evaluation datasets live under gitignored `knowledge/` and refer to catalogue
episodes by season and episode number. The loader resolves those positions to stable
UUIDs and rejects missing, overlapping, or duplicate cases before invoking an encoder.

The gate measures hit rate, mean reciprocal rank, and explicit forbidden-episode
leakage. Defaults are centralized at 0.80 hit rate, 0.60 MRR, and zero leaks. The CLI
prints one JSON report and exits nonzero when any threshold fails:

```shell
uv run python scripts/evaluate_retrieval.py \
  --catalogue-manifest knowledge/catalogue.json \
  --dataset knowledge/retrieval-evaluation.json
```

The evaluation command reads Qdrant and creates local query embeddings; it does not
mutate the collection or call OpenAI. Use the synthetic example only to understand the
contract—real questions and expected evidence remain private and gitignored.
