# Reviewed corpus ingestion

The ingestion command consumes three private inputs: a versioned catalogue manifest,
the promotion `review-ledger.json`, and its reviewed SRT directory. It verifies every
ledger hash and explicit filename-to-episode mapping before parsing or embedding text.

Validation is the default and does not contact Qdrant:

```shell
uv run python scripts/ingest_reviewed_corpus.py \
  --catalogue-manifest knowledge/catalogue.json \
  --review-ledger "knowledge/Modern_Family - season 1.en/reviewed/review-ledger.json" \
  --reviewed-directory "knowledge/Modern_Family - season 1.en/reviewed"
```

Add `--apply` only after validation succeeds. Apply mode checks/provisions the Qdrant
schema, creates local FastEmbed dense and sparse embeddings, and upserts deterministic
point IDs. It does not use the OpenAI API. Runtime location, collection, and credentials
come only from the centralized `CINEGRAPH_QDRANT_*` settings.
Local development uses Qdrant's embedded storage, which supports the same filters but
does not create payload indexes. Remote/production mode provisions all filter indexes.

The operation is replay-safe: source versions and transcript segments derive their IDs
from stable source identity and verified content. Qdrant upserts replace the same IDs
after a restart instead of creating duplicate evidence.
