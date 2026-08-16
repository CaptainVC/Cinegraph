# Jellyfin provider

The Jellyfin adapter maps CineGraph episode UUIDs to explicitly reviewed Jellyfin
item IDs. A connection is bound to one CineGraph profile and one Jellyfin user.
Changing the server, Jellyfin version, user, playback session, or item mapping changes
the connection revision and invalidates pending approvals.

Authentication is sent only in the `Authorization` header. Access tokens are excluded
from connection representations and must come from runtime secret provisioning; they
must never enter a LangGraph checkpoint, URL, log record, or repository file.

The adapter supports health, library/profile reads, played and favorite state,
playlist creation, and playback requests to an explicitly configured controllable
session. Every write is verified through a subsequent Jellyfin read. Transient reads
and idempotent writes use bounded exponential backoff; unsafe create/play commands are
not automatically retried. Repeated transient failures open an in-process circuit so
the RAG application can remain available while Jellyfin is degraded.

Automated tests use `httpx.MockTransport` with synthetic item IDs and run the same
provider contract as the mock adapter. A live Jellyfin smoke test remains an
environment-level gate once a synthetic Jellyfin server is provisioned.
