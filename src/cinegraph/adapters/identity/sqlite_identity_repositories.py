import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from uuid import UUID

from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.domain.enums.enum import AccountStatus, CorpusAccessMode, PrincipalKind
from cinegraph.domain.models.access import CorpusAccessScope, CorpusSeasonAccess
from cinegraph.domain.models.identity import SessionPrincipal, SessionRecord, UserAccount


class SqliteIdentityRepositories:
    # Persist accounts and token digests in one transactional development database.
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._migrate()

    def _migrate(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS user_accounts (
                    user_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    token_sha256 TEXT NOT NULL UNIQUE,
                    principal_kind TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    user_id TEXT NULL,
                    access_mode TEXT NOT NULL,
                    access_revision TEXT NOT NULL,
                    allowed_seasons_json TEXT NOT NULL,
                    unrestricted INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expires_at_idx
                    ON sessions(expires_at);
                """
            )

    def get_by_email(self, normalized_email: str) -> UserAccount | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM user_accounts WHERE email = ?",
                (normalized_email,),
            ).fetchone()
        return self._map_account(row) if row is not None else None

    def add(self, account: UserAccount) -> None:
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    INSERT INTO user_accounts (
                        user_id, profile_id, email, display_name,
                        password_hash, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(account.user_id),
                        str(account.profile_id),
                        account.email,
                        account.display_name,
                        account.password_hash,
                        account.status.value,
                        account.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                AuthenticationErrorMessages.EMAIL_ALREADY_REGISTERED
            ) from error

    def get_by_token_sha256(self, token_sha256: str) -> SessionRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE token_sha256 = ?",
                (token_sha256,),
            ).fetchone()
        return self._map_session(row) if row is not None else None

    def save(self, session: SessionRecord) -> None:
        allowed_seasons = json.dumps(
            [
                {
                    "series_id": str(item.series_id),
                    "season_number": item.season_number,
                }
                for item in sorted(
                    session.principal.corpus_access_scope.allowed_seasons
                )
            ],
            separators=(",", ":"),
        )
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO sessions (
                    session_id, token_sha256, principal_kind, profile_id,
                    user_id, access_mode, access_revision,
                    allowed_seasons_json, unrestricted,
                    created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token_sha256) DO UPDATE SET
                    revoked_at = excluded.revoked_at,
                    expires_at = excluded.expires_at
                """,
                (
                    str(session.session_id),
                    session.token_sha256,
                    session.principal.kind.value,
                    str(session.principal.profile_id),
                    (
                        str(session.principal.user_id)
                        if session.principal.user_id is not None
                        else None
                    ),
                    session.principal.corpus_access_scope.mode.value,
                    session.principal.corpus_access_scope.revision,
                    allowed_seasons,
                    int(session.principal.corpus_access_scope.unrestricted),
                    session.created_at.isoformat(),
                    session.expires_at.isoformat(),
                    (
                        session.revoked_at.isoformat()
                        if session.revoked_at is not None
                        else None
                    ),
                ),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _map_account(row: sqlite3.Row) -> UserAccount:
        return UserAccount(
            user_id=UUID(row["user_id"]),
            profile_id=UUID(row["profile_id"]),
            email=row["email"],
            display_name=row["display_name"],
            password_hash=row["password_hash"],
            status=AccountStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _map_session(row: sqlite3.Row) -> SessionRecord:
        allowed_seasons = frozenset(
            CorpusSeasonAccess(
                series_id=UUID(item["series_id"]),
                season_number=item["season_number"],
            )
            for item in json.loads(row["allowed_seasons_json"])
        )
        principal = SessionPrincipal(
            kind=PrincipalKind(row["principal_kind"]),
            profile_id=UUID(row["profile_id"]),
            user_id=UUID(row["user_id"]) if row["user_id"] is not None else None,
            corpus_access_scope=CorpusAccessScope(
                mode=CorpusAccessMode(row["access_mode"]),
                revision=row["access_revision"],
                allowed_seasons=allowed_seasons,
                unrestricted=bool(row["unrestricted"]),
            ),
        )
        return SessionRecord(
            session_id=UUID(row["session_id"]),
            token_sha256=row["token_sha256"],
            principal=principal,
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            revoked_at=(
                datetime.fromisoformat(row["revoked_at"])
                if row["revoked_at"] is not None
                else None
            ),
        )
