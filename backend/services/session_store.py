"""SQLite persistence for chat sessions and their messages."""

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services.audit_events import (
    mask_ip_address,
    normalize_user_agent_family,
    validate_event_metadata,
)


class FeedbackTargetNotFoundError(ValueError):
    """Raised when feedback does not target an assistant message in its session."""


class SessionOwnershipError(ValueError):
    """Raised when a conversation is missing or belongs to a different user."""


class ChatSessionStore:
    """Persist conversations independently from the vector database."""

    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL DEFAULT '',
                        email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'user'
                            CHECK (role IN ('user', 'admin')),
                        created_at TEXT NOT NULL,
                        email_verified INTEGER NOT NULL DEFAULT 0
                            CHECK (email_verified IN (0, 1)),
                        email_verified_at TEXT,
                        verification_token_hash TEXT,
                        verification_expires_at TEXT,
                        verification_sent_at TEXT,
                        pending_email TEXT,
                        email_change_token_hash TEXT,
                        email_change_expires_at TEXT,
                        email_change_sent_at TEXT,
                        auth_token_version INTEGER NOT NULL DEFAULT 0
                            CHECK (auth_token_version >= 0)
                    )
                """)
                self._add_column_if_missing(
                    connection,
                    "users",
                    "name",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._add_column_if_missing(
                    connection,
                    "users",
                    "email_verified",
                    "INTEGER NOT NULL DEFAULT 0 CHECK (email_verified IN (0, 1))",
                )
                self._add_column_if_missing(
                    connection,
                    "users",
                    "email_verified_at",
                    "TEXT",
                )
                self._add_column_if_missing(
                    connection,
                    "users",
                    "verification_token_hash",
                    "TEXT",
                )
                self._add_column_if_missing(
                    connection,
                    "users",
                    "verification_expires_at",
                    "TEXT",
                )
                self._add_column_if_missing(
                    connection,
                    "users",
                    "verification_sent_at",
                    "TEXT",
                )
                self._add_column_if_missing(
                    connection,
                    "users",
                    "pending_email",
                    "TEXT",
                )
                self._add_column_if_missing(
                    connection,
                    "users",
                    "email_change_token_hash",
                    "TEXT",
                )
                self._add_column_if_missing(
                    connection,
                    "users",
                    "email_change_expires_at",
                    "TEXT",
                )
                self._add_column_if_missing(
                    connection,
                    "users",
                    "email_change_sent_at",
                    "TEXT",
                )
                self._add_column_if_missing(
                    connection,
                    "users",
                    "auth_token_version",
                    "INTEGER NOT NULL DEFAULT 0 CHECK (auth_token_version >= 0)",
                )
                connection.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_verification_token
                    ON users(verification_token_hash)
                    WHERE verification_token_hash IS NOT NULL
                """)
                connection.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_change_token
                    ON users(email_change_token_hash)
                    WHERE email_change_token_hash IS NOT NULL
                """)
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        session_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL
                            REFERENCES users(user_id) ON DELETE CASCADE,
                        login_at TEXT NOT NULL,
                        last_activity_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        ip_address TEXT,
                        user_agent TEXT,
                        ip_masked TEXT,
                        user_agent_family TEXT,
                        revoked_at TEXT
                    )
                """)
                self._add_column_if_missing(
                    connection,
                    "auth_sessions",
                    "ip_masked",
                    "TEXT",
                )
                self._add_column_if_missing(
                    connection,
                    "auth_sessions",
                    "user_agent_family",
                    "TEXT",
                )
                self._sanitize_legacy_session_metadata(connection)
                connection.execute("""
                    CREATE TRIGGER IF NOT EXISTS
                        trg_auth_sessions_reject_raw_metadata_insert
                    BEFORE INSERT ON auth_sessions
                    WHEN NEW.ip_address IS NOT NULL OR NEW.user_agent IS NOT NULL
                    BEGIN
                        SELECT RAISE(ABORT, 'Raw session metadata is not allowed');
                    END
                """)
                connection.execute("""
                    CREATE TRIGGER IF NOT EXISTS
                        trg_auth_sessions_reject_raw_metadata_update
                    BEFORE UPDATE OF ip_address, user_agent ON auth_sessions
                    WHEN NEW.ip_address IS NOT NULL OR NEW.user_agent IS NOT NULL
                    BEGIN
                        SELECT RAISE(ABORT, 'Raw session metadata is not allowed');
                    END
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_active
                    ON auth_sessions(user_id, revoked_at, expires_at)
                """)
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS auth_audit_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL CHECK (
                            event_type IN (
                                'login_success', 'login_failed',
                                'session_created', 'session_revoked',
                                'logout_all_sessions', 'password_changed',
                                'password_reset_requested',
                                'password_reset_completed',
                                'email_change_requested', 'email_changed',
                                'account_deletion_requested', 'account_deleted'
                            )
                        ),
                        user_id TEXT,
                        occurred_at TEXT NOT NULL,
                        metadata_json TEXT NOT NULL CHECK (
                            length(CAST(metadata_json AS BLOB)) <= 1024
                        )
                    )
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_auth_audit_user_recent
                    ON auth_audit_events(user_id, occurred_at DESC)
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_auth_audit_event_recent
                    ON auth_audit_events(event_type, occurred_at DESC)
                """)
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS password_reset_tokens (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL UNIQUE
                            REFERENCES users(user_id) ON DELETE CASCADE,
                        expires_at TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_password_reset_expiry
                    ON password_reset_tokens(expires_at)
                """)
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                        session_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE
                    )
                """)
                self._add_column_if_missing(
                    connection,
                    "chat_sessions",
                    "user_id",
                    "TEXT REFERENCES users(user_id) ON DELETE CASCADE",
                )
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                        content TEXT NOT NULL,
                        sources_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
                            ON DELETE CASCADE
                    )
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chat_messages_session
                    ON chat_messages(session_id, id)
                """)
                connection.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_messages_id_session
                    ON chat_messages(id, session_id)
                """)
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS response_feedback (
                        feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        message_id INTEGER NOT NULL,
                        user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL,
                        rating TEXT NOT NULL
                            CHECK (rating IN ('helpful', 'not_helpful')),
                        comment TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE (session_id, message_id),
                        FOREIGN KEY (session_id)
                            REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                        FOREIGN KEY (message_id, session_id)
                            REFERENCES chat_messages(id, session_id) ON DELETE CASCADE
                    )
                """)
                self._add_column_if_missing(
                    connection,
                    "response_feedback",
                    "user_id",
                    "TEXT REFERENCES users(user_id) ON DELETE SET NULL",
                )
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_response_feedback_recent
                    ON response_feedback(updated_at DESC, feedback_id DESC)
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_recent
                    ON chat_sessions(user_id, updated_at DESC)
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_response_feedback_user_recent
                    ON response_feedback(user_id, updated_at DESC, feedback_id DESC)
                """)

    @staticmethod
    def _add_column_if_missing(
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table_name})")
        }
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )

    @staticmethod
    def _sanitize_legacy_session_metadata(
        connection: sqlite3.Connection,
    ) -> None:
        rows = connection.execute(
            """
            SELECT session_id, ip_address, user_agent, ip_masked,
                user_agent_family
            FROM auth_sessions
            WHERE ip_address IS NOT NULL OR user_agent IS NOT NULL
            """
        ).fetchall()
        for row in rows:
            connection.execute(
                """
                UPDATE auth_sessions
                SET ip_masked = COALESCE(ip_masked, ?),
                    user_agent_family = COALESCE(user_agent_family, ?),
                    ip_address = NULL,
                    user_agent = NULL
                WHERE session_id = ?
                """,
                (
                    mask_ip_address(row["ip_address"]),
                    normalize_user_agent_family(row["user_agent"]),
                    row["session_id"],
                ),
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _title(question: str) -> str:
        normalized = " ".join(question.split())
        if len(normalized) <= 60:
            return normalized
        return f"{normalized[:57].rstrip()}..."

    def create_user(
        self,
        user_id: str,
        email: str,
        password_hash: str,
        role: str,
        name: str = "",
        verification_token_hash: Optional[str] = None,
        verification_expires_at: Optional[str] = None,
        verification_sent_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert a user and return identity fields without the password hash."""
        if role not in ("user", "admin"):
            raise ValueError("Invalid user role.")
        created_at = self._now()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, name, email, password_hash, role, created_at,
                        email_verified, verification_token_hash,
                        verification_expires_at, verification_sent_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        user_id,
                        name,
                        email,
                        password_hash,
                        role,
                        created_at,
                        verification_token_hash,
                        verification_expires_at,
                        verification_sent_at,
                    ),
                )
        return {
            "user_id": user_id,
            "name": name,
            "email": email,
            "role": role,
            "created_at": created_at,
            "email_verified": False,
        }

    def get_user_for_auth_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Load a user row for internal password verification."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT user_id, name, email, password_hash, role, created_at,
                    email_verified, auth_token_version
                FROM users
                WHERE email = ? COLLATE NOCASE
                """,
                (email,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_user_for_auth_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Load internal authentication fields for one account."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT user_id, name, email, password_hash, role, created_at,
                    email_verified, auth_token_version
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_auth_session(
        self,
        session_id: str,
        user_id: str,
        login_at: str,
        expires_at: str,
        ip_address: Optional[str],
        user_agent: Optional[str],
        source: str = "unknown",
    ) -> None:
        """Persist a session with masked IP and normalized client metadata."""
        ip_masked = mask_ip_address(ip_address)
        user_agent_family = normalize_user_agent_family(user_agent)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM auth_sessions WHERE expires_at <= ?",
                    (login_at,),
                )
                connection.execute(
                    """
                    INSERT INTO auth_sessions
                        (session_id, user_id, login_at, last_activity_at,
                            expires_at, ip_masked, user_agent_family)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        user_id,
                        login_at,
                        login_at,
                        expires_at,
                        ip_masked,
                        user_agent_family,
                    ),
                )
                session_metadata = {
                    "source": source,
                    "ip_masked": ip_masked,
                    "user_agent_family": user_agent_family,
                }
                self._insert_audit_event(
                    connection,
                    "session_created",
                    {
                        **session_metadata,
                        "session_expires_at": expires_at,
                    },
                    user_id,
                    login_at,
                )
                self._insert_audit_event(
                    connection,
                    "login_success",
                    session_metadata,
                    user_id,
                    login_at,
                )

    def list_active_auth_sessions(
        self,
        user_id: str,
        now: str,
    ) -> List[Dict[str, Any]]:
        """Return only unexpired sessions owned by the selected user."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT session_id, login_at, last_activity_at, expires_at,
                    ip_masked, user_agent_family
                FROM auth_sessions
                WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?
                ORDER BY last_activity_at DESC, login_at DESC
                """,
                (user_id, now),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_audit_event(
        self,
        event_type: str,
        metadata: Dict[str, Any],
        user_id: Optional[str] = None,
        occurred_at: Optional[str] = None,
    ) -> None:
        """Validate and persist one allowlisted audit event."""
        with closing(self._connect()) as connection:
            with connection:
                self._insert_audit_event(
                    connection,
                    event_type,
                    metadata,
                    user_id,
                    occurred_at or self._now(),
                )

    def list_user_audit_events(
        self,
        user_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return a bounded activity history for one authenticated user."""
        if not 1 <= limit <= 100:
            raise ValueError("Activity limit must be between 1 and 100.")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT event_id, event_type, occurred_at, metadata_json
                FROM auth_audit_events
                WHERE user_id = ?
                ORDER BY occurred_at DESC, event_id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        events = []
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            validated_metadata = validate_event_metadata(
                row["event_type"],
                metadata,
            )
            events.append({
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "occurred_at": row["occurred_at"],
                "metadata": validated_metadata,
            })
        return events

    @staticmethod
    def _insert_audit_event(
        connection: sqlite3.Connection,
        event_type: str,
        metadata: Dict[str, Any],
        user_id: Optional[str],
        occurred_at: str,
    ) -> None:
        validated = validate_event_metadata(event_type, metadata)
        metadata_json = json.dumps(
            validated,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        try:
            safe_user_id = str(uuid.UUID(user_id)) if isinstance(user_id, str) else None
        except ValueError:
            safe_user_id = None
        connection.execute(
            """
            INSERT INTO auth_audit_events
                (event_type, user_id, occurred_at, metadata_json)
            VALUES (?, ?, ?, ?)
            """,
            (event_type, safe_user_id, occurred_at, metadata_json),
        )

    def touch_auth_session(
        self,
        session_id: str,
        user_id: str,
        now: str,
    ) -> bool:
        """Update activity only for an active session belonging to its owner."""
        with closing(self._connect()) as connection:
            with connection:
                updated = connection.execute(
                    """
                    UPDATE auth_sessions
                    SET last_activity_at = ?
                    WHERE session_id = ? AND user_id = ?
                        AND revoked_at IS NULL AND expires_at > ?
                    """,
                    (now, session_id, user_id, now),
                )
        return updated.rowcount == 1

    def revoke_auth_session(
        self,
        user_id: str,
        session_id: str,
        now: str,
    ) -> bool:
        """Revoke one active session only when it belongs to the caller."""
        with closing(self._connect()) as connection:
            with connection:
                revoked = connection.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = ?
                    WHERE user_id = ? AND session_id = ?
                        AND revoked_at IS NULL AND expires_at > ?
                    """,
                    (now, user_id, session_id, now),
                )
                if revoked.rowcount == 1:
                    self._insert_audit_event(
                        connection,
                        "session_revoked",
                        {
                            "reason_code": "user_initiated",
                            "revoked_by": "user",
                        },
                        user_id,
                        now,
                    )
        return revoked.rowcount == 1

    def update_display_name(self, user_id: str, name: str) -> bool:
        """Update only the selected account's public display name."""
        with closing(self._connect()) as connection:
            with connection:
                updated = connection.execute(
                    "UPDATE users SET name = ? WHERE user_id = ?",
                    (name, user_id),
                )
        return updated.rowcount == 1

    def issue_email_change(
        self,
        user_id: str,
        new_email: str,
        token_hash: str,
        expires_at: str,
        sent_at: str,
        source: str = "unknown",
    ) -> bool:
        """Replace one pending email change after checking current addresses."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                with connection:
                    user = connection.execute(
                        """
                        SELECT email FROM users
                        WHERE user_id = ? AND email_verified = 1
                        """,
                        (user_id,),
                    ).fetchone()
                    if user is None:
                        return False
                    existing = connection.execute(
                        """
                        SELECT user_id FROM users
                        WHERE email = ? COLLATE NOCASE AND user_id != ?
                        """,
                        (new_email, user_id),
                    ).fetchone()
                    if existing is not None or user["email"].lower() == new_email:
                        return False
                    updated = connection.execute(
                        """
                        UPDATE users
                        SET pending_email = ?,
                            email_change_token_hash = ?,
                            email_change_expires_at = ?,
                            email_change_sent_at = ?
                        WHERE user_id = ? AND email_verified = 1
                        """,
                        (new_email, token_hash, expires_at, sent_at, user_id),
                    )
                    if updated.rowcount == 1:
                        self._insert_audit_event(
                            connection,
                            "email_change_requested",
                            {"source": source},
                            user_id,
                            sent_at,
                        )
                    return updated.rowcount == 1
            except Exception:
                connection.rollback()
                raise

    def consume_email_change(
        self,
        token_hash: str,
        now: str,
        source: str = "unknown",
    ) -> bool:
        """Atomically change a verified address and revoke existing sessions."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                with connection:
                    user = connection.execute(
                        """
                        SELECT user_id, pending_email
                        FROM users
                        WHERE email_change_token_hash = ?
                            AND email_change_expires_at > ?
                            AND email_verified = 1
                        """,
                        (token_hash, now),
                    ).fetchone()
                    if user is None or not user["pending_email"]:
                        return False
                    existing = connection.execute(
                        """
                        SELECT user_id FROM users
                        WHERE email = ? COLLATE NOCASE AND user_id != ?
                        """,
                        (user["pending_email"], user["user_id"]),
                    ).fetchone()
                    if existing is not None:
                        connection.execute(
                            """
                            UPDATE users
                            SET pending_email = NULL,
                                email_change_token_hash = NULL,
                                email_change_expires_at = NULL,
                                email_change_sent_at = NULL
                            WHERE user_id = ? AND email_change_token_hash = ?
                            """,
                            (user["user_id"], token_hash),
                        )
                        return False
                    updated = connection.execute(
                        """
                        UPDATE users
                        SET email = pending_email,
                            pending_email = NULL,
                            email_change_token_hash = NULL,
                            email_change_expires_at = NULL,
                            email_change_sent_at = NULL,
                            auth_token_version = auth_token_version + 1
                        WHERE user_id = ? AND email_change_token_hash = ?
                            AND email_change_expires_at > ?
                            AND email_verified = 1
                        """,
                        (user["user_id"], token_hash, now),
                    )
                    if updated.rowcount == 1:
                        revoked = connection.execute(
                            """
                            UPDATE auth_sessions
                            SET revoked_at = ?
                            WHERE user_id = ? AND revoked_at IS NULL
                            """,
                            (now, user["user_id"]),
                        )
                        if revoked.rowcount:
                            self._insert_audit_event(
                                connection,
                                "session_revoked",
                                {
                                    "reason_code": "security_action",
                                    "revoked_by": "system",
                                },
                                user["user_id"],
                                now,
                            )
                        self._insert_audit_event(
                            connection,
                            "email_changed",
                            {"source": source},
                            user["user_id"],
                            now,
                        )
                    return updated.rowcount == 1
            except sqlite3.IntegrityError:
                connection.rollback()
                return False
            except Exception:
                connection.rollback()
                raise

    def delete_user(self, user_id: str, password_hash: str) -> bool:
        """Delete a password-confirmed account and its owned dependent data."""
        with closing(self._connect()) as connection:
            with connection:
                active_sessions = connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM auth_sessions
                    WHERE user_id = ? AND revoked_at IS NULL
                    """,
                    (user_id,),
                ).fetchone()["count"]
                deleted = connection.execute(
                    """
                    DELETE FROM users
                    WHERE user_id = ? AND password_hash = ?
                    """,
                    (user_id, password_hash),
                )
                if deleted.rowcount == 1 and active_sessions:
                    self._insert_audit_event(
                        connection,
                        "session_revoked",
                        {
                            "reason_code": "security_action",
                            "revoked_by": "user",
                        },
                        user_id,
                        self._now(),
                    )
                if deleted.rowcount == 1:
                    self._insert_audit_event(
                        connection,
                        "account_deleted",
                        {"deletion_scope": "completed"},
                        None,
                        self._now(),
                    )
        return deleted.rowcount == 1

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Load only safe identity fields for authorization."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT user_id, name, email, role, created_at, email_verified
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        user = dict(row)
        user["email_verified"] = bool(user["email_verified"])
        return user

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Load safe identity fields for an operator-run account promotion."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT user_id, name, email, role, created_at, email_verified
                FROM users
                WHERE email = ? COLLATE NOCASE
                """,
                (email,),
            ).fetchone()
        if row is None:
            return None
        user = dict(row)
        user["email_verified"] = bool(user["email_verified"])
        return user

    def promote_user_to_admin(self, email: str) -> bool:
        """Promote one exact verified account; never create or guess an account."""
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE users
                    SET role = 'admin'
                    WHERE email = ? COLLATE NOCASE AND role = 'user'
                        AND email_verified = 1
                    """,
                    (email,),
                )
        return cursor.rowcount == 1

    def issue_email_verification(
        self,
        email: str,
        token_hash: str,
        expires_at: str,
        sent_at: str,
    ) -> Optional[str]:
        """Replace an unverified user's token and invalidate any previous token."""
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE users
                    SET verification_token_hash = ?,
                        verification_expires_at = ?,
                        verification_sent_at = ?
                    WHERE email = ? COLLATE NOCASE AND email_verified = 0
                    """,
                    (token_hash, expires_at, sent_at, email),
                )
                if cursor.rowcount != 1:
                    return None
                row = connection.execute(
                    "SELECT email FROM users WHERE email = ? COLLATE NOCASE",
                    (email,),
                ).fetchone()
        return row["email"]

    def consume_email_verification(
        self,
        token_hash: str,
        now: str,
    ) -> bool:
        """Verify and consume a matching, unexpired token exactly once."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                with connection:
                    row = connection.execute(
                        """
                        SELECT user_id
                        FROM users
                        WHERE verification_token_hash = ?
                            AND email_verified = 0
                            AND verification_expires_at > ?
                        """,
                        (token_hash, now),
                    ).fetchone()
                    if row is None:
                        return False
                    cursor = connection.execute(
                        """
                        UPDATE users
                        SET email_verified = 1,
                            email_verified_at = ?,
                            verification_token_hash = NULL,
                            verification_expires_at = NULL,
                            verification_sent_at = NULL
                        WHERE user_id = ? AND verification_token_hash = ?
                            AND email_verified = 0
                            AND verification_expires_at > ?
                        """,
                        (now, row["user_id"], token_hash, now),
                    )
                    return cursor.rowcount == 1
            except Exception:
                connection.rollback()
                raise

    def issue_password_reset(
        self,
        email: str,
        token_hash: str,
        expires_at: str,
        created_at: str,
    ) -> Optional[str]:
        """Replace the active reset token for a verified account."""
        with closing(self._connect()) as connection:
            with connection:
                row = connection.execute(
                    """
                    SELECT user_id, email
                    FROM users
                    WHERE email = ? COLLATE NOCASE AND email_verified = 1
                    """,
                    (email,),
                ).fetchone()
                if row is None:
                    return None
                connection.execute(
                    """
                    INSERT INTO password_reset_tokens
                        (token_hash, user_id, expires_at, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        token_hash = excluded.token_hash,
                        expires_at = excluded.expires_at,
                        created_at = excluded.created_at
                    """,
                    (token_hash, row["user_id"], expires_at, created_at),
                )
        return row["email"]

    def consume_password_reset(
        self,
        token_hash: str,
        password_hash: str,
        now: str,
        source: str = "unknown",
        ip_masked: Optional[str] = None,
    ) -> bool:
        """Atomically update the password and consume one unexpired token."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                with connection:
                    row = connection.execute(
                        """
                        SELECT user_id
                        FROM password_reset_tokens
                        WHERE token_hash = ? AND expires_at > ?
                        """,
                        (token_hash, now),
                    ).fetchone()
                    if row is None:
                        return False
                    updated = connection.execute(
                        """
                        UPDATE users
                        SET password_hash = ?,
                            auth_token_version = auth_token_version + 1
                        WHERE user_id = ?
                        """,
                        (password_hash, row["user_id"]),
                    )
                    if updated.rowcount != 1:
                        return False
                    revoked = connection.execute(
                        """
                        UPDATE auth_sessions
                        SET revoked_at = ?
                        WHERE user_id = ? AND revoked_at IS NULL
                        """,
                        (now, row["user_id"]),
                    )
                    if revoked.rowcount:
                        self._insert_audit_event(
                            connection,
                            "session_revoked",
                            {
                                "reason_code": "password_reset",
                                "revoked_by": "system",
                            },
                            row["user_id"],
                            now,
                        )
                    consumed = connection.execute(
                        """
                        DELETE FROM password_reset_tokens
                        WHERE token_hash = ? AND user_id = ? AND expires_at > ?
                        """,
                        (token_hash, row["user_id"], now),
                    )
                    if consumed.rowcount != 1:
                        raise sqlite3.IntegrityError(
                            "Password reset token could not be consumed."
                        )
                    self._insert_audit_event(
                        connection,
                        "password_reset_completed",
                        {"source": source, "ip_masked": ip_masked},
                        row["user_id"],
                        now,
                    )
                    return True
            except Exception:
                connection.rollback()
                raise

    def update_password(
        self,
        user_id: str,
        current_password_hash: str,
        new_password_hash: str,
        current_session_id: Optional[str],
        now: str,
        expires_at: str,
        source: str = "unknown",
    ) -> Optional[int]:
        """Update a password, revoke other sessions, and rotate the token version."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                with connection:
                    if current_session_id is not None:
                        active_session = connection.execute(
                            """
                            SELECT 1 FROM auth_sessions
                            WHERE session_id = ? AND user_id = ?
                                AND revoked_at IS NULL AND expires_at > ?
                            """,
                            (current_session_id, user_id, now),
                        ).fetchone()
                        if active_session is None:
                            return None

                    updated = connection.execute(
                        """
                        UPDATE users
                        SET password_hash = ?,
                            auth_token_version = auth_token_version + 1
                        WHERE user_id = ? AND password_hash = ?
                        """,
                        (
                            new_password_hash,
                            user_id,
                            current_password_hash,
                        ),
                    )
                    self._insert_audit_event(
                    connection,
                    "password_changed",
                    {"source": source},
                    user_id,
                    now,
                    )
                    if updated.rowcount != 1:
                        return None
                    revoked = connection.execute(
                        """
                        UPDATE auth_sessions
                        SET revoked_at = ?
                        WHERE user_id = ? AND revoked_at IS NULL
                            AND (? IS NULL OR session_id != ?)
                        """,
                        (now, user_id, current_session_id, current_session_id),
                    )
                    if revoked.rowcount:
                        self._insert_audit_event(
                            connection,
                            "session_revoked",
                            {
                                "reason_code": "security_action",
                                "revoked_by": "user",
                            },
                            user_id,
                            now,
                        )
                    if current_session_id is not None:
                        connection.execute(
                            """
                            UPDATE auth_sessions
                            SET last_activity_at = ?, expires_at = ?
                            WHERE session_id = ? AND user_id = ?
                                AND revoked_at IS NULL AND expires_at > ?
                            """,
                            (
                                now,
                                expires_at,
                                current_session_id,
                                user_id,
                                now,
                            ),
                        )
                    row = connection.execute(
                        "SELECT auth_token_version FROM users WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    return int(row["auth_token_version"])
            except Exception:
                connection.rollback()
                raise

    def revoke_all_user_sessions(
        self,
        user_id: str,
        source: str = "unknown",
    ) -> Optional[int]:
        """Advance the account token version and revoke all active sessions."""
        now = self._now()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                with connection:
                    updated = connection.execute(
                        """
                        UPDATE users
                        SET auth_token_version = auth_token_version + 1
                        WHERE user_id = ?
                        """,
                        (user_id,),
                    )
                    if updated.rowcount != 1:
                        return None
                    revoked = connection.execute(
                        """
                        UPDATE auth_sessions
                        SET revoked_at = ?
                        WHERE user_id = ? AND revoked_at IS NULL
                        """,
                        (now, user_id),
                    )
                    if revoked.rowcount:
                        self._insert_audit_event(
                            connection,
                            "session_revoked",
                            {
                                "reason_code": "user_initiated",
                                "revoked_by": "user",
                            },
                            user_id,
                            now,
                        )
                    self._insert_audit_event(
                        connection,
                        "logout_all_sessions",
                        {
                            "revoked_session_count": max(revoked.rowcount, 0),
                            "source": source,
                        },
                        user_id,
                        now,
                    )
                    row = connection.execute(
                        "SELECT auth_token_version FROM users WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    return int(row["auth_token_version"])
            except Exception:
                connection.rollback()
                raise

    def save_exchange(
        self,
        session_id: str,
        user_id: str,
        question: str,
        answer: str,
        sources: List[Dict[str, Any]],
    ) -> int:
        """Atomically persist one exchange and return its assistant message ID."""
        now = self._now()
        with closing(self._connect()) as connection:
            with connection:
                existing = connection.execute(
                    "SELECT user_id FROM chat_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if existing is not None and existing["user_id"] != user_id:
                    raise SessionOwnershipError(
                        "Conversation not found or unavailable."
                    )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO chat_sessions
                        (session_id, title, created_at, updated_at, user_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, self._title(question), now, now, user_id),
                )

                connection.execute(
                    """
                    INSERT INTO chat_messages
                        (session_id, role, content, sources_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, "user", question, "[]", now),
                )
                assistant_cursor = connection.execute(
                    """
                    INSERT INTO chat_messages
                        (session_id, role, content, sources_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        "assistant",
                        answer,
                        json.dumps(sources, ensure_ascii=False),
                        now,
                    ),
                )
                connection.execute(
                    "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
                    (now, session_id),
                )
        return int(assistant_cursor.lastrowid)

    def get_messages(self, session_id: str, user_id: str) -> List[Dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT messages.id AS message_id, messages.role, messages.content,
                    messages.sources_json, messages.created_at
                FROM chat_messages AS messages
                JOIN chat_sessions AS sessions
                    ON sessions.session_id = messages.session_id
                WHERE messages.session_id = ? AND sessions.user_id = ?
                ORDER BY messages.id
                """,
                (session_id, user_id),
            ).fetchall()
        return [
            {
                "message_id": row["message_id"],
                "role": row["role"],
                "content": row["content"],
                "sources": json.loads(row["sources_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def submit_feedback(
        self,
        session_id: str,
        user_id: str,
        message_id: int,
        rating: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update the single feedback record for an assistant response."""
        if rating not in ("helpful", "not_helpful"):
            raise ValueError("Invalid feedback rating.")
        if comment is not None and not isinstance(comment, str):
            raise ValueError("Feedback comment must be text.")

        normalized_comment = comment.strip() if comment is not None else ""
        if len(normalized_comment) > 1000:
            raise ValueError("Feedback comment must be 1000 characters or fewer.")
        normalized_comment = normalized_comment or None

        now = self._now()
        with closing(self._connect()) as connection:
            with connection:
                target = connection.execute(
                    """
                    SELECT role
                    FROM chat_messages AS messages
                    JOIN chat_sessions AS sessions
                        ON sessions.session_id = messages.session_id
                    WHERE messages.id = ? AND messages.session_id = ?
                        AND sessions.user_id = ?
                    """,
                    (message_id, session_id, user_id),
                ).fetchone()
                if target is None or target["role"] != "assistant":
                    raise FeedbackTargetNotFoundError(
                        "Session or assistant response not found."
                    )

                connection.execute(
                    """
                    INSERT INTO response_feedback
                        (session_id, message_id, user_id, rating, comment,
                            created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (session_id, message_id) DO UPDATE SET
                        user_id = excluded.user_id,
                        rating = excluded.rating,
                        comment = COALESCE(
                            excluded.comment, response_feedback.comment
                        ),
                        updated_at = excluded.updated_at
                    """,
                    (
                        session_id,
                        message_id,
                        user_id,
                        rating,
                        normalized_comment,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT feedback_id, session_id, message_id, rating, comment,
                        created_at, updated_at
                    FROM response_feedback
                    WHERE session_id = ? AND message_id = ?
                    """,
                    (session_id, message_id),
                ).fetchone()
        return dict(row)

    def get_feedback_summary(
        self,
        limit: int = 20,
        rating: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return aggregate counts and a bounded, newest-first feedback list."""
        if not 1 <= limit <= 100:
            raise ValueError("Feedback limit must be between 1 and 100.")
        if rating is not None and rating not in ("helpful", "not_helpful"):
            raise ValueError("Invalid feedback rating filter.")

        with closing(self._connect()) as connection:
            counts = connection.execute(
                """
                SELECT COUNT(*) AS total_feedback,
                    COALESCE(SUM(CASE WHEN rating = 'helpful' THEN 1 ELSE 0 END), 0)
                        AS helpful_count,
                    COALESCE(SUM(CASE WHEN rating = 'not_helpful' THEN 1 ELSE 0 END), 0)
                        AS not_helpful_count
                FROM response_feedback
                WHERE user_id IS NOT NULL
                """
            ).fetchone()

            if rating is None:
                rows = connection.execute(
                    """
                    SELECT feedback_id, session_id, message_id, rating, comment,
                        created_at, updated_at
                    FROM response_feedback
                    WHERE user_id IS NOT NULL
                    ORDER BY updated_at DESC, feedback_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT feedback_id, session_id, message_id, rating, comment,
                        created_at, updated_at
                    FROM response_feedback
                    WHERE user_id IS NOT NULL AND rating = ?
                    ORDER BY updated_at DESC, feedback_id DESC
                    LIMIT ?
                    """,
                    (rating, limit),
                ).fetchall()

        total = counts["total_feedback"]
        helpful = counts["helpful_count"]
        return {
            "total_feedback": total,
            "helpful_count": helpful,
            "not_helpful_count": counts["not_helpful_count"],
            "positive_percentage": round(helpful * 100 / total, 2) if total else 0.0,
            "recent_feedback": [dict(row) for row in rows],
        }

    def get_session(
        self,
        session_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT session_id, title, created_at, updated_at
                FROM chat_sessions
                WHERE session_id = ? AND user_id = ?
                """,
                (session_id, user_id),
            ).fetchone()
        if row is None:
            return None
        session = dict(row)
        session["messages"] = self.get_messages(session_id, user_id)
        return session

    def list_sessions(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT session_id, title, created_at, updated_at,
                    (SELECT COUNT(*) FROM chat_messages
                     WHERE chat_messages.session_id = chat_sessions.session_id) AS message_count
                FROM chat_sessions
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def session_exists_for_another_user(self, session_id: str, user_id: str) -> bool:
        """Detect an existing conversation that the caller cannot claim."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT user_id FROM chat_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row is not None and row["user_id"] != user_id

    def delete_session(self, session_id: str, user_id: str) -> bool:
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM chat_sessions WHERE session_id = ? AND user_id = ?",
                    (session_id, user_id),
                )
        return cursor.rowcount > 0

    def clear_all(self, user_id: str) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM chat_sessions WHERE user_id = ?",
                    (user_id,),
                )
