# Series metadata ingestion

CineGraph currently acquires series artwork, regular cast, and episode guest cast
from TVmaze only. The adapter requires an explicit numeric TVmaze show ID (Modern
Family is `80`) and compares the resolved title to the catalogue using a normalized
case-insensitive exact comparison. It never fuzzy-matches titles or silently chooses a
provider record.

[TVmaze](https://www.tvmaze.com/api) is the initial provider because its public API is
CC BY-SA 4.0, supplies show image URLs, main cast, and per-episode guest cast, and
permits caching/hotlinking subject to attribution and share-alike obligations. Every
exported snapshot includes attribution, license, canonical URLs, and source-version
provenance. TMDB is intentionally not implemented because its current
[API terms](https://www.themoviedb.org/api-terms-of-use) prohibit AI-app usage; IMDb
[free-data terms](https://help.imdb.com/article/imdb/general-information/can-i-use-imdb-data-in-my-software/G5JTRESSHJBBHTGX)
are not suitable for a public multi-user product.

Regular cast is a show-level credit and is not an assertion that a person appears in
every episode. Guest credits are episode-specific and are retained in provider order.
Later transcript-speaker reconciliation can confirm regular-cast appearances against
actual episode dialogue without changing this source's semantics.

Each acquisition is canonicalized and SHA-256 hashed. Identical active content is
idempotent; changed content creates a pending source version linked to its parent, and
the in-memory adapter retires the prior active version using compare-and-swap checks.
Only active versions with an approved review status are visible through the read port.
New versions are never silently marked reviewed. Poster image dimensions may be absent;
the canonical and medium/original URLs and rights metadata remain mandatory when a
poster exists.

`scripts/ingest_tvmaze_series_metadata.py` loads the gitignored catalogue manifest,
fetches only that series' catalogue episodes, and writes a deterministic raw-payload-free
JSON snapshot to `knowledge/series-metadata/pending/`. Existing differing output is
protected unless `--force` is supplied. The export includes acquisition and poster
retrieval timestamps for provenance. A subsequent fetch with the same semantic content
compares the stored content hash and is a no-op even though those timestamps differ.

The publication workflow is deliberately explicit and zero-token:

```powershell
uv run python scripts/ingest_tvmaze_series_metadata.py `
  --manifest knowledge/catalogue.json `
  --series-id <catalogue-series-uuid> `
  --tvmaze-show-id 80 `
  --output knowledge/series-metadata/pending/modern-family.json

uv run python scripts/review_series_metadata_snapshot.py `
  --manifest knowledge/catalogue.json `
  --input knowledge/series-metadata/pending/modern-family.json
```

The reviewer validates the canonical SHA-256 content hash, source-version identity,
active/allowed TVmaze provenance, exact catalogue episode reconciliation, and trusted
HTTPS hosts. Episode identity is exact by catalogue IDs and season/episode position;
provider title comparison is case-insensitive but never fuzzy, so harmless title-case
differences do not block publication. It then downloads an original poster with a bounded timeout and size limit,
falls back to TVmaze's medium image when needed, validates both MIME and magic bytes,
atomically writes `knowledge/series-metadata/artwork/<series-id>.poster`, and atomically
publishes the reviewed JSON to `knowledge/series-metadata/approved/`. Existing artifacts
are idempotent when their content hash matches and are protected from replacement unless
`--force` is supplied. The runtime loader reads only the approved directory; a missing
approved directory means there is no metadata enrichment, while malformed files fail
closed.

Artwork is served by the application from the entitlement-checked, same-origin artwork
directory. Its default cache policy is `private, max-age=86400`; it is never a public
shared-cache artifact because the same path policy will also protect future
authenticated-only series.

Structured metadata is reviewed deterministically instead of with an LLM because its
acceptance criteria are machine-checkable identity, provenance, rights, hashes, and
schema invariants. This is cheaper, reproducible, and avoids allowing a model to invent
or silently alter cast, episode, or licensing facts. LLM/LangGraph review remains
appropriate for genuinely interpretive material such as subtitle speaker attribution,
but does not replace these publication gates. The CLI repository is intentionally
in-memory for this phase; the domain service and repository contract are complete, while
durable cross-invocation storage remains a later infrastructure concern.
