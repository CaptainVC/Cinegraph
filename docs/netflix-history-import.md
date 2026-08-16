# Netflix viewing-history import

CineGraph accepts the profile-scoped CSV that Netflix makes available from Viewing
Activity. It never asks for a Netflix password, cookie, access token, or live account
connection.

The authenticated application service treats every upload as untrusted input. It
enforces a safe filename, an allowlisted CSV content type, a byte and row limit,
UTF-8/BOM decoding, exact `Title,Date` headers, bounded cells, supported date formats,
and formula-like cell rejection. Raw upload bytes are hashed for idempotency and are
never persisted.

Title resolution is deterministic. Season/episode identifiers and exact normalized
catalogue titles produce candidates; repeated titles remain ambiguous. No resolution,
including a unique match, updates watch progress until the user explicitly approves
the row-to-canonical-episode mapping. Unknown or invented approvals fail closed.

Committed imports create `netflix_csv` watch events and retain only the content hash,
counts, approved canonical episode IDs, and completion time. Pending normalized titles
expire after the configured retention window. Re-importing or recommitting the same
file is idempotent and creates no duplicate watch events.
