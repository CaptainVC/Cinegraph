# Mock media provider

`MockMediaProvider` is a deterministic developer/demo adapter. It never contacts or
controls a real media server, and its health response labels it as simulated.

The adapter is seeded explicitly with synthetic episodes and permitted profile IDs.
It supports library reads, profile snapshots, watched/favorite updates, playlists,
playback-request recording, idempotent command replay, and post-write verification.

Tests can configure latency, unavailable state, selected command failures, connection
revision drift, stale writes, and verification failures. The reusable provider contract
in `tests/contracts/media_provider_contract.py` must also pass for real adapters.
