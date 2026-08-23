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
JSON snapshot. Existing differing output is protected unless `--force` is supplied.
The export includes acquisition and poster retrieval timestamps for provenance. A
subsequent fetch with the same semantic content compares the stored content hash and
is a no-op even though those timestamps would differ. The CLI repository is intentionally in-memory for this phase; the domain service and
repository contract are complete, while durable cross-invocation storage remains a
later infrastructure concern.
