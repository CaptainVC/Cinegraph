# Authentication and guest sessions

Cinegraph issues opaque 256-bit session tokens. Only each token's SHA-256 digest is
stored; the raw value is returned once to the caller and will be placed in an
HTTP-only cookie by the API layer. Passwords use independently salted scrypt hashes
with constant-time digest comparison. Unknown accounts and wrong passwords return the
same credential error.

Guest sessions expire after eight hours and carry the immutable guest corpus scope,
which permits only Modern Family seasons 1 and 2. Authenticated sessions expire after
14 days and carry an unrestricted corpus scope. Revocation is persisted and survives
application restarts.

Development stores accounts, password hashes, session digests, and revocation state in
the gitignored SQLAlchemy SQLite database derived from
`CINEGRAPH_IDENTITY_DATABASE_PATH`. Run the documented Alembic migration command before
starting a new database. The application never calls `create_all` or silently migrates
at startup.

Production must set `CINEGRAPH_DATABASE_URL` to a `postgresql+psycopg://` URL. The
checked-in Alembic migrations own schema evolution for both SQLite development and
PostgreSQL production. Credentials are secret configuration and are never logged.

Never put raw session tokens, password values, or password hashes in logs. The API
slice must use secure, HTTP-only, same-site cookies and must not expose whether an email
exists during login.
