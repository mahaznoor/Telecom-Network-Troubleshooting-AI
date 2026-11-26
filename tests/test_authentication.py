"""Focused tests for account authentication, migrations, and ownership checks."""

import sqlite3
import hashlib
import json
import tempfile
import unittest
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from streamlit.testing.v1 import AppTest

from backend.admin_bootstrap import promote_verified_account
from backend.api.auth import (
    RegistrationRequest,
    get_auth_service,
    get_email_service,
    get_verification_email_limiter,
)
from backend.api.routes import get_chat_service
from backend.config import settings
from backend.main import create_app, validate_deployment_configuration
from backend.services.auth_service import (
    AuthConfigurationError,
    AuthenticationService,
    EmailAlreadyInUseError,
    EmailAlreadyRegisteredError,
    EmailNotVerifiedError,
    InvalidCurrentPasswordError,
    InvalidCredentialsError,
    PasswordReuseError,
)
from backend.services.chat_service import ChatService
from backend.services.email_service import EmailService, SMTPConfigurationError
from backend.services.audit_events import (
    EVENT_METADATA_ALLOWLIST,
    AuditMetadataValidationError,
    hash_login_identifier,
    mask_ip_address,
    normalize_user_agent_family,
    validate_event_metadata,
)
from backend.services.login_limiter import (
    LoginRateLimitExceeded,
    LoginRateLimiter,
    get_login_rate_limiter,
)
from backend.services.session_store import (
    ChatSessionStore,
    FeedbackTargetNotFoundError,
    SessionOwnershipError,
)
from backend.services.verification_limiter import VerificationEmailRateLimiter
from frontend.components.api_client import API_BASE_URL, call_api


_JWT_SECRET = "test-signing-key-that-is-at-least-32-bytes-long"
_AUDIT_SECRET = "dedicated-audit-hmac-secret-that-is-at-least-32-bytes"
_PASSWORD = "correct horse battery staple"
_RESET_PASSWORD = "Strong Correct Horse 47!"
_RESET_PASSWORD_2 = "Another Strong Horse 58!"
_RESET_PASSWORD_3 = "Fresh Strong Horse 69!"
_ENTRYPOINT = Path(__file__).resolve().parents[1] / "frontend" / "streamlit_app.py"


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


class FakeEmailService:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    @staticmethod
    def require_configuration() -> None:
        return None

    def send_verification(self, recipient: str, token: str) -> None:
        self.sent.append((recipient, token))

    def send_password_reset(self, recipient: str, token: str) -> None:
        self.sent.append((recipient, token))

    def send_email_change(self, recipient: str, token: str) -> None:
        self.sent.append((recipient, token))


class UnconfiguredEmailService:
    @staticmethod
    def require_configuration() -> None:
        from backend.services.email_service import SMTPConfigurationError

        raise SMTPConfigurationError("SMTP settings are missing.")

    @staticmethod
    def send_verification(recipient: str, token: str) -> None:
        raise AssertionError("Email must not be sent without SMTP configuration.")

    @staticmethod
    def send_password_reset(recipient: str, token: str) -> None:
        raise AssertionError("Email must not be sent without SMTP configuration.")

    @staticmethod
    def send_email_change(recipient: str, token: str) -> None:
        raise AssertionError("Email must not be sent without SMTP configuration.")


class AuditEventMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings_patch = patch.multiple(
            settings,
            JWT_SECRET_KEY=_JWT_SECRET,
            BCRYPT_ROUNDS=4,
            AUDIT_IDENTIFIER_HMAC_SECRET=SecretStr(_AUDIT_SECRET),
        )
        self.settings_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ChatSessionStore(
            str(Path(self.temp_dir.name) / "chat_history.sqlite3")
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        self.settings_patch.stop()

    def test_every_event_accepts_only_its_declared_fields(self) -> None:
        metadata_by_event = {
            "login_success": {
                "source": "web",
                "ip_masked": "192.0.2.0/24",
                "user_agent_family": "Chrome",
            },
            "login_failed": {
                "source": "api",
                "ip_masked": "2001:db8:1234::/48",
                "user_agent_family": "Firefox",
                "reason_code": "invalid_credentials",
                "identifier_hash": "a" * 64,
            },
            "session_created": {
                "source": "unknown",
                "ip_masked": "192.0.2.0/24",
                "user_agent_family": "Unknown",
                "session_expires_at": "2026-11-27T00:00:00Z",
            },
            "session_revoked": {
                "reason_code": "user_initiated",
                "revoked_by": "user",
            },
            "logout_all_sessions": {
                "revoked_session_count": 0,
                "source": "api",
            },
            "password_changed": {"source": "web"},
            "password_reset_requested": {
                "source": "api",
                "ip_masked": "192.0.2.0/24",
            },
            "password_reset_completed": {
                "source": "web",
                "ip_masked": "2001:db8:1234::/48",
            },
            "email_change_requested": {"source": "web"},
            "email_changed": {"source": "api"},
            "account_deletion_requested": {"source": "web"},
            "account_deleted": {"deletion_scope": "completed"},
        }

        self.assertEqual(set(metadata_by_event), set(EVENT_METADATA_ALLOWLIST))
        for event_type, metadata in metadata_by_event.items():
            with self.subTest(event_type=event_type):
                validated = validate_event_metadata(event_type, metadata)
                self.assertLessEqual(
                    set(validated),
                    EVENT_METADATA_ALLOWLIST[event_type],
                )
                self.store.record_audit_event(event_type, metadata)

        with closing(self.store._connect()) as connection:
            rows = connection.execute(
                "SELECT event_type, metadata_json FROM auth_audit_events"
            ).fetchall()
        self.assertEqual(len(rows), len(EVENT_METADATA_ALLOWLIST))
        for row in rows:
            self.assertLessEqual(
                set(json.loads(row["metadata_json"])),
                EVENT_METADATA_ALLOWLIST[row["event_type"]],
            )

    def test_unknown_events_and_metadata_keys_are_rejected_without_writes(self) -> None:
        with self.assertRaises(AuditMetadataValidationError):
            self.store.record_audit_event("unknown_event", {})
        with self.assertRaises(AuditMetadataValidationError):
            self.store.record_audit_event(
                "login_success",
                {"source": "api", "email": "worker@example.com"},
            )
        with closing(self.store._connect()) as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM auth_audit_events"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_invalid_field_values_are_rejected(self) -> None:
        invalid_metadata = [
            ("login_success", {"source": "mobile"}),
            ("login_success", {"source": None}),
            ("login_success", {"ip_masked": "192.0.2.25"}),
            ("login_success", {"ip_masked": "2001:db8::/64"}),
            (
                "login_success",
                {"user_agent_family": "Mozilla/5.0 Chrome/125"},
            ),
            ("login_failed", {"reason_code": "database error"}),
            ("login_failed", {"identifier_hash": "not-a-hash"}),
            ("session_created", {"session_expires_at": "2026-11-27T00:00:00"}),
            (
                "session_created",
                {"session_expires_at": "2026-11-27T00:00:00+01:00"},
            ),
            ("logout_all_sessions", {"revoked_session_count": True}),
            ("logout_all_sessions", {"revoked_session_count": -1}),
            ("session_revoked", {"revoked_by": "unknown"}),
            ("account_deleted", {"deletion_scope": "worker@example.com"}),
        ]
        for event_type, metadata in invalid_metadata:
            with self.subTest(event_type=event_type, metadata=metadata):
                with self.assertRaises(AuditMetadataValidationError):
                    validate_event_metadata(event_type, metadata)

    def test_metadata_size_is_limited_in_sqlite_as_well_as_by_validator(self) -> None:
        with closing(self.store._connect()) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO auth_audit_events
                        (event_type, occurred_at, metadata_json)
                    VALUES ('account_deleted', ?, ?)
                    """,
                    (
                        "2026-11-27T00:00:00Z",
                        '{"payload":"' + ("x" * 1025) + '"}',
                    ),
                )

    def test_identifier_hash_is_keyed_and_normalizes_login_identifier(self) -> None:
        first = hash_login_identifier(" Worker@Example.com ", _AUDIT_SECRET)
        second = hash_login_identifier("worker@example.com", _AUDIT_SECRET)
        plain_hash = hashlib.sha256(b"worker@example.com").hexdigest()

        self.assertEqual(first, second)
        self.assertNotEqual(first, plain_hash)
        self.assertEqual(len(first), 64)

    def test_ip_and_user_agent_normalizers_only_return_safe_categories(self) -> None:
        self.assertEqual(mask_ip_address("192.0.2.25"), "192.0.2.0/24")
        self.assertEqual(
            mask_ip_address("2001:db8:1234:5678::1"),
            "2001:db8:1234::/48",
        )
        self.assertIsNone(mask_ip_address("not-an-ip"))
        self.assertEqual(
            normalize_user_agent_family(
                "Mozilla/5.0 Chrome/125.0.0.0 Safari/537.36"
            ),
            "Chrome",
        )
        self.assertEqual(normalize_user_agent_family("unrecognized client"), "Unknown")
        self.assertIsNone(normalize_user_agent_family(None))

    def test_legacy_session_metadata_is_masked_in_additive_migration(self) -> None:
        user, _ = self._registered_user()
        session_id = "s" * 40
        now = datetime.now(timezone.utc).isoformat()
        self.store.create_auth_session(
            session_id,
            user["user_id"],
            now,
            (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            None,
            None,
        )
        raw_user_agent = "Mozilla/5.0 Firefox/125.0"
        with closing(self.store._connect()) as connection:
            with connection:
                connection.execute(
                    "DROP TRIGGER trg_auth_sessions_reject_raw_metadata_update"
                )
                connection.execute(
                    """
                    UPDATE auth_sessions
                    SET ip_address = ?, user_agent = ?
                    WHERE session_id = ?
                    """,
                    ("192.0.2.25", raw_user_agent, session_id),
                )

        ChatSessionStore(str(self.store.database_path))

        with closing(self.store._connect()) as connection:
            migrated = connection.execute(
                "SELECT * FROM auth_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        self.assertIsNone(migrated["ip_address"])
        self.assertIsNone(migrated["user_agent"])
        self.assertEqual(migrated["ip_masked"], "192.0.2.0/24")
        self.assertEqual(migrated["user_agent_family"], "Firefox")
        with closing(self.store._connect()) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE auth_sessions SET user_agent = ?
                    WHERE session_id = ?
                    """,
                    ("Mozilla/5.0 Firefox/125.0", session_id),
                )

    def test_login_events_never_persist_email_ip_or_raw_user_agent(self) -> None:
        user, verification_token = self._registered_user()
        self.assertTrue(self._auth.verify_email(verification_token))
        email = user["email"]
        raw_ip = "2001:db8:1234:5678:abcd:ef01:2345:6789"
        raw_user_agent = (
            "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )

        with patch.object(
            settings,
            "AUDIT_IDENTIFIER_HMAC_SECRET",
            SecretStr(_AUDIT_SECRET),
        ):
            with self.assertRaises(InvalidCredentialsError):
                self._auth.login(
                    email,
                    "wrong password value",
                    client_ip=raw_ip,
                    user_agent=raw_user_agent,
                    source="web",
                )
            result = self._auth.login(
                email,
                _PASSWORD,
                client_ip=raw_ip,
                user_agent=raw_user_agent,
                source="web",
            )
            reset_request = self._auth.request_password_reset(
                email,
                source="web",
                client_ip=raw_ip,
            )

        with closing(self.store._connect()) as connection:
            events = connection.execute(
                """
                SELECT event_type, metadata_json
                FROM auth_audit_events ORDER BY event_id
                """
            ).fetchall()
            stored_session = connection.execute(
                "SELECT * FROM auth_sessions WHERE user_id = ?",
                (user["user_id"],),
            ).fetchone()
        serialized_events = "\n".join(
            row["metadata_json"] for row in events
        )
        self.assertNotIn(email, serialized_events)
        self.assertNotIn(raw_ip, serialized_events)
        self.assertNotIn(raw_user_agent, serialized_events)
        self.assertNotIn("wrong password value", serialized_events)
        self.assertNotIn(result["access_token"], serialized_events)
        self.assertNotIn(verification_token, serialized_events)
        self.assertNotIn(reset_request[1], serialized_events)
        self.assertIn('"ip_masked":"2001:db8:1234::/48"', serialized_events)
        self.assertIn('"user_agent_family":"Chrome"', serialized_events)

        failed = json.loads(
            next(
                row["metadata_json"]
                for row in events
                if row["event_type"] == "login_failed"
            )
        )
        self.assertEqual(
            failed["identifier_hash"],
            hash_login_identifier(email, _AUDIT_SECRET),
        )
        self.assertIsNone(stored_session["ip_address"])
        self.assertIsNone(stored_session["user_agent"])
        self.assertEqual(stored_session["ip_masked"], "2001:db8:1234::/48")
        self.assertEqual(stored_session["user_agent_family"], "Chrome")
        self.assertTrue(result["access_token"])

    def test_all_sensitive_input_names_are_unknown_metadata(self) -> None:
        sensitive_metadata = {
            "headers": {"authorization": "Bearer example"},
            "cookies": "session-cookie",
            "authorization": "Bearer example",
            "password": _PASSWORD,
            "jwt": "signed-token",
            "reset_token": "reset-token",
            "verification_token": "verification-token",
            "email": "worker@example.com",
            "ip_address": "192.0.2.25",
            "user_agent": "Mozilla/5.0 Chrome/125",
        }
        for key, value in sensitive_metadata.items():
            with self.subTest(key=key):
                with self.assertRaises(AuditMetadataValidationError):
                    self.store.record_audit_event(
                        "login_success",
                        {"source": "api", key: value},
                    )

        with closing(self.store._connect()) as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM auth_audit_events"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def _registered_user(self):
        self._auth = AuthenticationService(self.store)
        return self._auth.register(
            "worker@example.com",
            _PASSWORD,
            "Worker",
        )

class AuthenticationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings_patch = patch.multiple(
            settings,
            JWT_SECRET_KEY=_JWT_SECRET,
            JWT_ACCESS_TOKEN_MINUTES=60,
            BCRYPT_ROUNDS=4,
            ENVIRONMENT="development",
            CORS_ORIGINS=["http://localhost:8501"],
        )
        self.settings_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = str(Path(self.temp_dir.name) / "chat_history.sqlite3")
        self.store = ChatSessionStore(database_path)
        self.auth = AuthenticationService(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        self.settings_patch.stop()

    def test_public_registration_never_assigns_admin_and_passwords_are_hashed(self) -> None:
        claimed_admin, token = self.auth.register(
            " ADMIN@example.com ", _PASSWORD, "Admin Claim"
        )
        regular_user, _ = self.auth.register(
            "worker@example.com", _PASSWORD, "Worker"
        )
        stored_user = self.store.get_user_for_auth_by_email(claimed_admin["email"])

        self.assertEqual(claimed_admin["role"], "user")
        self.assertFalse(claimed_admin["email_verified"])
        self.assertEqual(regular_user["role"], "user")
        self.assertNotEqual(stored_user["password_hash"], _PASSWORD)
        self.assertTrue(stored_user["password_hash"].startswith("$2"))
        self.assertNotIn("password_hash", claimed_admin)
        with closing(self.store._connect()) as connection:
            token_hash = connection.execute(
                "SELECT verification_token_hash FROM users WHERE user_id = ?",
                (claimed_admin["user_id"],),
            ).fetchone()["verification_token_hash"]
        self.assertEqual(len(token_hash), 64)
        self.assertNotEqual(token_hash, token)
        self.assertNotIn(token, token_hash)
        self.assertNotIn("role", jwt.decode(
            self.auth.create_access_token(claimed_admin["user_id"]),
            _JWT_SECRET,
            algorithms=["HS256"],
            issuer="telecom-network-troubleshooting-ai",
        ))

    def test_registration_is_regular_and_duplicate_email_is_rejected(self) -> None:
        user, _ = self.auth.register("person@example.com", _PASSWORD, "Person")
        self.assertEqual(user["role"], "user")
        with self.assertRaises(EmailAlreadyRegisteredError):
            self.auth.register("PERSON@example.com", _PASSWORD, "Person")

    def test_operator_confirmation_promotes_only_the_selected_existing_account(self) -> None:
        selected, selected_token = self.auth.register(
            "verified@example.com", _PASSWORD, "Verified"
        )
        other, _ = self.auth.register("other@example.com", _PASSWORD, "Other")
        with self.assertRaises(ValueError):
            promote_verified_account(
                self.store,
                selected["email"],
                "PROMOTE verified@example.com",
            )
        self.auth.verify_email(selected_token)

        with self.assertRaises(ValueError):
            promote_verified_account(
                self.store,
                selected["email"],
                "PROMOTE other@example.com",
            )
        self.assertEqual(self.store.get_user_by_id(selected["user_id"])["role"], "user")

        self.assertTrue(
            promote_verified_account(
                self.store,
                selected["email"],
                "PROMOTE verified@example.com",
            )
        )
        self.assertEqual(self.store.get_user_by_id(selected["user_id"])["role"], "admin")
        self.assertEqual(self.store.get_user_by_id(other["user_id"])["role"], "user")
        with self.assertRaises(ValueError):
            promote_verified_account(
                self.store,
                "missing@example.com",
                "PROMOTE missing@example.com",
            )

    def test_login_returns_short_lived_token_and_rejects_bad_credentials(self) -> None:
        created, token = self.auth.register(
            "worker@example.com", _PASSWORD, "Worker"
        )
        with self.assertRaises(EmailNotVerifiedError):
            self.auth.login("worker@example.com", _PASSWORD)
        self.assertTrue(self.auth.verify_email(token))
        login = self.auth.login("WORKER@example.com", _PASSWORD)

        self.assertEqual(login["user"]["user_id"], created["user_id"])
        self.assertEqual(login["expires_in"], 60 * 60)
        self.assertEqual(
            self.auth.resolve_access_token(login["access_token"])["user_id"],
            created["user_id"],
        )
        self.assertEqual(
            jwt.decode(
                login["access_token"],
                _JWT_SECRET,
                algorithms=["HS256"],
                issuer="telecom-network-troubleshooting-ai",
            )["auth_token_version"],
            0,
        )
        with self.assertRaises(InvalidCredentialsError):
            self.auth.login("worker@example.com", "incorrect password")

    def test_password_change_requires_current_and_rejects_reuse(self) -> None:
        user, verification_token = self.auth.register(
            "worker@example.com", _RESET_PASSWORD, "Worker"
        )
        self.assertTrue(self.auth.verify_email(verification_token))
        original_token = self.auth.login(
            "worker@example.com",
            _RESET_PASSWORD,
        )["access_token"]

        with self.assertRaises(InvalidCurrentPasswordError):
            self.auth.change_password(
                user["user_id"],
                "incorrect current password",
                _RESET_PASSWORD_2,
            )
        with self.assertRaises(PasswordReuseError):
            self.auth.change_password(
                user["user_id"],
                _RESET_PASSWORD,
                _RESET_PASSWORD,
            )

        version = self.auth.change_password(
            user["user_id"],
            _RESET_PASSWORD,
            _RESET_PASSWORD_2,
        )
        self.assertEqual(version, 1)
        self.assertIsNone(self.auth.resolve_access_token(original_token))
        with self.assertRaises(InvalidCredentialsError):
            self.auth.login("worker@example.com", _RESET_PASSWORD)

        self.assertEqual(
            self.auth.login(
                "worker@example.com",
                _RESET_PASSWORD_2,
            )["user"]["user_id"],
            user["user_id"],
        )
        version = self.auth.change_password(
            user["user_id"],
            _RESET_PASSWORD_2,
            _RESET_PASSWORD_3,
            revoke_other_sessions=True,
        )
        renewed_token = self.auth.create_access_token(user["user_id"], version)
        self.assertEqual(version, 2)
        self.assertIsNone(self.auth.resolve_access_token(original_token))
        self.assertIsNotNone(self.auth.resolve_access_token(renewed_token))

    def test_login_persists_session_metadata_without_storing_the_jwt(self) -> None:
        user, verification_token = self.auth.register(
            "worker@example.com",
            _PASSWORD,
            "Worker",
        )
        self.assertTrue(self.auth.verify_email(verification_token))
        login = self.auth.login(
            "worker@example.com",
            _PASSWORD,
            client_ip="192.0.2.25",
            user_agent="Mozilla/5.0 Chrome/125.0.0.0 Safari/537.36",
        )
        payload = jwt.decode(
            login["access_token"],
            _JWT_SECRET,
            algorithms=["HS256"],
            issuer="telecom-network-troubleshooting-ai",
        )
        session_id = payload["sid"]
        sessions = self.auth.list_active_sessions(user["user_id"])
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], session_id)
        self.assertEqual(sessions[0]["ip_masked"], "192.0.2.0/24")
        self.assertEqual(sessions[0]["user_agent_family"], "Chrome")
        self.assertGreaterEqual(
            sessions[0]["last_activity_at"],
            sessions[0]["login_at"],
        )
        self.assertEqual(
            self.auth.resolve_access_token(login["access_token"])["session_id"],
            session_id,
        )
        with closing(self.store._connect()) as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(auth_sessions)")
            }
            stored_session = connection.execute(
                "SELECT * FROM auth_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        self.assertNotIn("access_token", columns)
        self.assertNotIn(login["access_token"], str(dict(stored_session)))
        self.assertIsNone(stored_session["ip_address"])
        self.assertIsNone(stored_session["user_agent"])
        self.assertEqual(stored_session["ip_masked"], "192.0.2.0/24")
        self.assertEqual(stored_session["user_agent_family"], "Chrome")

    def test_email_change_is_hashed_single_use_and_revokes_sessions(self) -> None:
        user, verification_token = self.auth.register(
            "worker@example.com",
            _RESET_PASSWORD,
            "Worker",
        )
        self.assertTrue(self.auth.verify_email(verification_token))
        with closing(self.store._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE users SET role = 'admin' WHERE user_id = ?",
                    (user["user_id"],),
                )
        access_token = self.auth.login(
            "worker@example.com",
            _RESET_PASSWORD,
        )["access_token"]

        with self.assertRaises(EmailAlreadyInUseError):
            self.auth.request_email_change(
                user["user_id"],
                _RESET_PASSWORD,
                "worker@example.com",
            )
        recipient, change_token = self.auth.request_email_change(
            user["user_id"],
            _RESET_PASSWORD,
            "new-worker@example.com",
        )
        self.assertEqual(recipient, "new-worker@example.com")
        with closing(self.store._connect()) as connection:
            pending = connection.execute(
                """
                SELECT pending_email, email_change_token_hash FROM users
                WHERE user_id = ?
                """,
                (user["user_id"],),
            ).fetchone()
        self.assertEqual(pending["pending_email"], recipient)
        self.assertEqual(
            pending["email_change_token_hash"],
            hashlib.sha256(change_token.encode("utf-8")).hexdigest(),
        )
        self.assertNotEqual(pending["email_change_token_hash"], change_token)
        self.assertEqual(
            self.store.get_user_by_id(user["user_id"])["email"],
            "worker@example.com",
        )

        self.assertTrue(self.auth.verify_email_change(change_token))
        self.assertFalse(self.auth.verify_email_change(change_token))
        self.assertIsNone(self.auth.resolve_access_token(access_token))
        updated_user = self.store.get_user_by_id(user["user_id"])
        self.assertEqual(updated_user["email"], recipient)
        self.assertEqual(updated_user["name"], "Worker")
        self.assertEqual(updated_user["role"], "admin")
        self.assertEqual(
            self.auth.login(recipient, _RESET_PASSWORD)["user"]["role"],
            "admin",
        )

    def test_expired_email_change_token_does_not_change_address(self) -> None:
        user, verification_token = self.auth.register(
            "worker@example.com",
            _RESET_PASSWORD,
            "Worker",
        )
        self.assertTrue(self.auth.verify_email(verification_token))
        _, token = self.auth.request_email_change(
            user["user_id"],
            _RESET_PASSWORD,
            "new-worker@example.com",
        )
        with closing(self.store._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE users
                    SET email_change_expires_at = '2000-01-01T00:00:00+00:00'
                    WHERE user_id = ?
                    """,
                    (user["user_id"],),
                )

        self.assertFalse(self.auth.verify_email_change(token))
        self.assertEqual(
            self.store.get_user_by_id(user["user_id"])["email"],
            "worker@example.com",
        )


    def test_session_revocation_survives_restart_and_handles_legacy_tokens(self) -> None:
        user, verification_token = self.auth.register(
            "worker@example.com", _PASSWORD, "Worker"
        )
        self.assertTrue(self.auth.verify_email(verification_token))
        legacy_token = jwt.encode(
            {
                "sub": user["user_id"],
                "iss": "telecom-network-troubleshooting-ai",
                "iat": datetime.now(timezone.utc),
                "exp": datetime.now(timezone.utc) + timedelta(minutes=60),
            },
            _JWT_SECRET,
            algorithm="HS256",
        )
        self.assertIsNotNone(self.auth.resolve_access_token(legacy_token))

        self.assertTrue(self.auth.revoke_all_sessions(user["user_id"]))
        restarted_auth = AuthenticationService(
            ChatSessionStore(str(self.store.database_path))
        )
        self.assertIsNone(restarted_auth.resolve_access_token(legacy_token))
        fresh_login = restarted_auth.login("worker@example.com", _PASSWORD)
        claims = jwt.decode(
            fresh_login["access_token"],
            _JWT_SECRET,
            algorithms=["HS256"],
            issuer="telecom-network-troubleshooting-ai",
        )
        self.assertEqual(claims["auth_token_version"], 1)

    def test_invalid_expired_tokens_and_missing_secret_fail_closed(self) -> None:
        user, verification_token = self.auth.register(
            "worker@example.com", _PASSWORD, "Worker"
        )
        self.assertFalse(self.auth.verify_email("invalid-token"))
        self.assertTrue(self.auth.verify_email(verification_token))
        self.assertFalse(self.auth.verify_email(verification_token))
        expired_token = jwt.encode(
            {
                "sub": user["user_id"],
                "iss": "telecom-network-troubleshooting-ai",
                "iat": 1,
                "exp": 2,
            },
            _JWT_SECRET,
            algorithm="HS256",
        )

        self.assertIsNone(self.auth.resolve_access_token("not-a-jwt"))
        self.assertIsNone(self.auth.resolve_access_token(expired_token))
        with patch.object(settings, "JWT_SECRET_KEY", ""):
            with self.assertRaises(AuthConfigurationError):
                self.auth.register("other@example.com", _PASSWORD, "Other")

    def test_expired_verification_token_is_rejected(self) -> None:
        user, token = self.auth.register(
            "worker@example.com", _PASSWORD, "Worker"
        )
        with closing(self.store._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE users
                    SET verification_expires_at = '2000-01-01T00:00:00+00:00'
                    WHERE user_id = ?
                    """,
                    (user["user_id"],),
                )
        self.assertFalse(self.auth.verify_email(token))
        self.assertFalse(
            self.store.get_user_by_id(user["user_id"])["email_verified"]
        )

    def test_resending_replaces_the_prior_single_use_token(self) -> None:
        user, old_token = self.auth.register(
            "worker@example.com", _PASSWORD, "Worker"
        )
        resent = self.auth.resend_verification("worker@example.com")
        self.assertIsNotNone(resent)
        self.assertEqual(resent[0], "worker@example.com")
        new_token = resent[1]
        self.assertNotEqual(old_token, new_token)
        self.assertFalse(self.auth.verify_email(old_token))
        self.assertTrue(self.auth.verify_email(new_token))
        self.assertTrue(
            self.store.get_user_by_id(user["user_id"])["email_verified"]
        )

    def test_password_reset_hashes_replaces_and_consumes_single_use_tokens(self) -> None:
        user, verification_token = self.auth.register(
            "worker@example.com", _PASSWORD, "Worker"
        )
        self.assertTrue(self.auth.verify_email(verification_token))

        first_reset = self.auth.request_password_reset("worker@example.com")
        self.assertIsNotNone(first_reset)
        first_token = first_reset[1]
        with closing(self.store._connect()) as connection:
            first_hash = connection.execute(
                """
                SELECT token_hash FROM password_reset_tokens WHERE user_id = ?
                """,
                (user["user_id"],),
            ).fetchone()["token_hash"]
        self.assertEqual(
            first_hash,
            hashlib.sha256(first_token.encode("utf-8")).hexdigest(),
        )
        self.assertNotEqual(first_hash, first_token)

        second_reset = self.auth.request_password_reset("worker@example.com")
        self.assertIsNotNone(second_reset)
        second_token = second_reset[1]
        self.assertFalse(self.auth.reset_password(first_token, _RESET_PASSWORD))
        self.assertTrue(self.auth.reset_password(second_token, _RESET_PASSWORD))
        self.assertFalse(self.auth.reset_password(second_token, _RESET_PASSWORD))
        with self.assertRaises(InvalidCredentialsError):
            self.auth.login("worker@example.com", _PASSWORD)
        self.assertEqual(
            self.auth.login("worker@example.com", _RESET_PASSWORD)["user"]["user_id"],
            user["user_id"],
        )

    def test_expired_password_reset_token_does_not_change_password(self) -> None:
        _, verification_token = self.auth.register(
            "worker@example.com", _PASSWORD, "Worker"
        )
        self.assertTrue(self.auth.verify_email(verification_token))
        reset = self.auth.request_password_reset("worker@example.com")
        self.assertIsNotNone(reset)
        with closing(self.store._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE password_reset_tokens
                    SET expires_at = '2000-01-01T00:00:00+00:00'
                    """
                )

        self.assertFalse(self.auth.reset_password(reset[1], _RESET_PASSWORD))
        self.assertEqual(
            self.auth.login("worker@example.com", _PASSWORD)["user"]["email"],
            "worker@example.com",
        )

    def test_new_password_requires_multiple_character_classes(self) -> None:
        with self.assertRaises(ValueError):
            self.auth.reset_password("not-a-token", "all lowercase password")

    def test_registration_rejects_role_injection_and_invalid_password_lengths(self) -> None:
        with self.assertRaises(ValidationError):
            RegistrationRequest(
                email="worker@example.com",
                password=_PASSWORD,
                role="admin",
            )
        with self.assertRaises(ValidationError):
            RegistrationRequest(email="worker@example.com", password="short")
        with self.assertRaises(ValidationError):
            RegistrationRequest(email="not-an-email", password=_PASSWORD)


class VerificationEmailServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings_patch = patch.multiple(
            settings,
            ENVIRONMENT="development",
            SMTP_HOST="smtp.example.test",
            SMTP_PORT=587,
            SMTP_USERNAME="mailer",
            SMTP_PASSWORD=SecretStr("smtp-password-for-test"),
            SMTP_FROM_EMAIL="no-reply@example.test",
            SMTP_USE_TLS=True,
            FRONTEND_BASE_URL="http://localhost:8501",
            EMAIL_VERIFICATION_MINUTES=20,
        )
        self.settings_patch.start()

    def tearDown(self) -> None:
        self.settings_patch.stop()

    def test_smtp_delivery_uses_configured_tls_and_verification_link(self) -> None:
        with patch("backend.services.email_service.smtplib.SMTP") as smtp_factory:
            smtp_client = smtp_factory.return_value.__enter__.return_value
            EmailService().send_verification(
                "person@example.test",
                "safe-token_123",
            )

        smtp_factory.assert_called_once_with(
            "smtp.example.test",
            587,
            timeout=15,
        )
        smtp_client.starttls.assert_called_once()
        smtp_client.login.assert_called_once_with(
            "mailer",
            "smtp-password-for-test",
        )
        message = smtp_client.send_message.call_args.args[0]
        self.assertEqual(message["To"], "person@example.test")
        self.assertIn(
            "http://localhost:8501?verify_email=safe-token_123",
            message.get_content(),
        )

    def test_smtp_delivery_uses_password_reset_link(self) -> None:
        with patch("backend.services.email_service.smtplib.SMTP") as smtp_factory:
            smtp_client = smtp_factory.return_value.__enter__.return_value
            EmailService().send_password_reset(
                "person@example.test",
                "reset-token_123",
            )

        message = smtp_client.send_message.call_args.args[0]
        self.assertEqual(message["To"], "person@example.test")
        self.assertIn(
            "http://localhost:8501?reset_password=reset-token_123",
            message.get_content(),
        )
        self.assertIn("30 minutes", message.get_content())

    def test_smtp_delivery_uses_email_change_verification_link(self) -> None:
        with patch("backend.services.email_service.smtplib.SMTP") as smtp_factory:
            smtp_client = smtp_factory.return_value.__enter__.return_value
            EmailService().send_email_change(
                "new-person@example.test",
                "email-change-token_123",
            )

        message = smtp_client.send_message.call_args.args[0]
        self.assertEqual(message["To"], "new-person@example.test")
        self.assertIn(
            "http://localhost:8501?verify_email_change=email-change-token_123",
            message.get_content(),
        )
        self.assertIn("20 minutes", message.get_content())

    def test_smtp_configuration_requires_sender_and_safe_production_transport(self) -> None:
        with patch.object(settings, "SMTP_HOST", ""):
            with self.assertRaises(SMTPConfigurationError):
                EmailService.require_configuration()

        with patch.multiple(
            settings,
            ENVIRONMENT="production",
            FRONTEND_BASE_URL="http://localhost:8501",
            SMTP_USE_TLS=False,
        ):
            with self.assertRaises(SMTPConfigurationError):
                EmailService.require_configuration()


class OwnershipAndMigrationTests(unittest.TestCase):
    def test_existing_users_are_preserved_but_migrated_as_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = str(Path(temp_dir) / "chat_history.sqlite3")
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                CREATE TABLE users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO users (user_id, email, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("legacy-admin", "admin@example.com", "existing-hash", "admin", "2025-01-01"),
            )
            connection.commit()
            connection.close()

            store = ChatSessionStore(database_path)
            legacy_user = store.get_user_for_auth_by_email("admin@example.com")

            self.assertEqual(legacy_user["user_id"], "legacy-admin")
            self.assertEqual(legacy_user["password_hash"], "existing-hash")
            self.assertEqual(legacy_user["role"], "admin")
            self.assertEqual(legacy_user["auth_token_version"], 0)
            self.assertFalse(legacy_user["email_verified"])
            with closing(store._connect()) as migrated_connection:
                token_hash = migrated_connection.execute(
                    "SELECT verification_token_hash FROM users WHERE user_id = ?",
                    ("legacy-admin",),
                ).fetchone()["verification_token_hash"]
                reset_table = migrated_connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name = 'password_reset_tokens'
                    """
                ).fetchone()
                auth_sessions_table = migrated_connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name = 'auth_sessions'
                    """
                ).fetchone()
                migrated_columns = {
                    row["name"]
                    for row in migrated_connection.execute(
                        "PRAGMA table_info(users)"
                    )
                }
            self.assertIsNone(token_hash)
            self.assertIsNotNone(reset_table)
            self.assertIsNotNone(auth_sessions_table)
            self.assertTrue(
                {
                    "pending_email",
                    "email_change_token_hash",
                    "email_change_expires_at",
                    "email_change_sent_at",
                }.issubset(migrated_columns)
            )

    def test_legacy_rows_are_preserved_but_remain_unassigned_and_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = str(Path(temp_dir) / "chat_history.sqlite3")
            legacy_session = str(uuid.uuid4())
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE response_feedback (
                    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    rating TEXT NOT NULL,
                    comment TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(session_id, message_id)
                );
                """
            )
            connection.execute(
                "INSERT INTO chat_sessions VALUES (?, ?, ?, ?)",
                (legacy_session, "Legacy chat", "created", "updated"),
            )
            connection.executemany(
                """
                INSERT INTO chat_messages
                    (session_id, role, content, sources_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (legacy_session, "user", "Question", "[]", "created"),
                    (legacy_session, "assistant", "Answer", "[]", "created"),
                ],
            )
            connection.execute(
                """
                INSERT INTO response_feedback
                    (session_id, message_id, rating, comment, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (legacy_session, 2, "helpful", "legacy note", "created", "updated"),
            )
            connection.commit()
            connection.close()

            store = ChatSessionStore(database_path)
            user_id = str(uuid.uuid4())
            store.create_user(user_id, "new@example.com", "bcrypt-hash", "user")

            with closing(sqlite3.connect(database_path)) as migrated:
                session_owner = migrated.execute(
                    "SELECT user_id FROM chat_sessions WHERE session_id = ?",
                    (legacy_session,),
                ).fetchone()[0]
                feedback = migrated.execute(
                    """
                    SELECT user_id, comment FROM response_feedback
                    WHERE session_id = ?
                    """,
                    (legacy_session,),
                ).fetchone()

            self.assertIsNone(session_owner)
            self.assertEqual(feedback, (None, "legacy note"))
            self.assertIsNone(store.get_session(legacy_session, user_id))
            self.assertEqual(store.list_sessions(user_id), [])
            self.assertTrue(
                store.session_exists_for_another_user(legacy_session, user_id)
            )
            self.assertEqual(store.get_feedback_summary()["total_feedback"], 0)
            with self.assertRaises(SessionOwnershipError):
                store.save_exchange(
                    legacy_session, user_id, "new question", "new answer", []
                )

    def test_users_cannot_read_write_or_clear_another_users_sessions_or_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChatSessionStore(
                str(Path(temp_dir) / "chat_history.sqlite3")
            )
            user_a = str(uuid.uuid4())
            user_b = str(uuid.uuid4())
            store.create_user(user_a, "a@example.com", "hash", "user")
            store.create_user(user_b, "b@example.com", "hash", "user")
            session_a = str(uuid.uuid4())
            session_b = str(uuid.uuid4())
            store.save_exchange(session_a, user_a, "A question", "A answer", [])
            store.save_exchange(session_b, user_b, "B question", "B answer", [])
            message_b = store.get_messages(session_b, user_b)[1]["message_id"]

            self.assertEqual(
                [item["session_id"] for item in store.list_sessions(user_a)],
                [session_a],
            )
            self.assertIsNone(store.get_session(session_b, user_a))
            self.assertEqual(store.get_messages(session_b, user_a), [])
            self.assertFalse(store.delete_session(session_b, user_a))
            self.assertTrue(store.session_exists_for_another_user(session_b, user_a))
            with self.assertRaises(SessionOwnershipError):
                store.save_exchange(session_b, user_a, "intrusion", "answer", [])
            with self.assertRaises(FeedbackTargetNotFoundError):
                store.submit_feedback(session_b, user_a, message_b, "helpful")

            store.clear_all(user_a)
            self.assertEqual(store.list_sessions(user_a), [])
            self.assertIsNotNone(store.get_session(session_b, user_b))


class AuthenticationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings_patch = patch.multiple(
            settings,
            JWT_SECRET_KEY=_JWT_SECRET,
            JWT_ACCESS_TOKEN_MINUTES=60,
            BCRYPT_ROUNDS=4,
            ENVIRONMENT="development",
            CORS_ORIGINS=["http://localhost:8501"],
        )
        self.settings_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ChatSessionStore(
            str(Path(self.temp_dir.name) / "chat_history.sqlite3")
        )
        self.auth = AuthenticationService(self.store)
        self.chat_service = object.__new__(ChatService)
        self.chat_service.session_store = self.store
        self.app = create_app()
        self.app.dependency_overrides[get_auth_service] = lambda: self.auth
        self.app.dependency_overrides[get_chat_service] = lambda: self.chat_service
        self.email_service = FakeEmailService()
        self.app.dependency_overrides[get_email_service] = (
            lambda: self.email_service
        )
        self.verification_limiter = VerificationEmailRateLimiter()
        self.app.dependency_overrides[get_verification_email_limiter] = (
            lambda: self.verification_limiter
        )
        self.login_limiter = LoginRateLimiter()
        self.app.dependency_overrides[get_login_rate_limiter] = (
            lambda: self.login_limiter
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.app.dependency_overrides.clear()
        self.temp_dir.cleanup()
        self.settings_patch.stop()

    def _register(self, email: str):
        return self.client.post(
            "/api/v1/auth/register",
            json={
                "name": "Test User",
                "email": email,
                "password": _PASSWORD,
            },
        )

    def _verify(self, email: str):
        token = next(
            token
            for recipient, token in reversed(self.email_service.sent)
            if recipient.lower() == email.lower()
        )
        return self.client.post(
            "/api/v1/auth/verify-email",
            json={"token": token},
        )

    def _stored_user(self, email: str):
        return self.store.get_user_for_auth_by_email(email)

    def test_registration_requires_single_use_verification_before_login(self) -> None:
        registered = self._register("worker@example.com")
        user = self._stored_user("worker@example.com")
        self.assertEqual(registered.status_code, 202)
        self.assertFalse(user["email_verified"])
        self.assertNotIn("password_hash", registered.text)
        self.assertNotIn(self.email_service.sent[0][1], registered.text)

        blocked = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(
            blocked.json()["detail"],
            "Verify your email address before signing in.",
        )
        self.assertNotIn("access_token", blocked.json())

        token = self.email_service.sent[0][1]
        verified = self.client.post(
            "/api/v1/auth/verify-email",
            json={"token": token},
        )
        repeated = self.client.post(
            "/api/v1/auth/verify-email",
            json={"token": token},
        )
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(repeated.status_code, 400)
        self.assertNotIn("SQL", repeated.text)
        self.assertNotIn(token, verified.text)

        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        )
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()["user"]["email_verified"])
        self.assertNotIn("password_hash", login.text)

    def test_resend_invalidates_previous_token_without_revealing_account_state(self) -> None:
        self._register("worker@example.com")
        old_token = self.email_service.sent[-1][1]
        resend = self.client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "worker@example.com"},
        )
        self.assertEqual(resend.status_code, 202)
        new_token = self.email_service.sent[-1][1]
        self.assertNotEqual(old_token, new_token)
        self.assertNotIn(new_token, resend.text)

        old_result = self.client.post(
            "/api/v1/auth/verify-email",
            json={"token": old_token},
        )
        new_result = self.client.post(
            "/api/v1/auth/verify-email",
            json={"token": new_token},
        )
        self.assertEqual(old_result.status_code, 400)
        self.assertEqual(new_result.status_code, 200)

        sent_before_unknown = len(self.email_service.sent)
        unknown_resend = self.client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "unknown@example.com"},
        )
        self.assertEqual(unknown_resend.status_code, resend.status_code)
        self.assertEqual(unknown_resend.json(), resend.json())
        self.assertEqual(len(self.email_service.sent), sent_before_unknown)

    def test_password_reset_responses_are_generic_and_token_is_single_use(self) -> None:
        self._register("worker@example.com")
        self.assertEqual(self._verify("worker@example.com").status_code, 200)
        previous_session = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        ).json()["access_token"]

        reset = self.client.post(
            "/api/v1/auth/request-password-reset",
            json={"email": "worker@example.com"},
        )
        delivered_token = self.email_service.sent[-1][1]
        unknown = self.client.post(
            "/api/v1/auth/request-password-reset",
            json={"email": "unknown@example.com"},
        )
        self.assertEqual(reset.status_code, 202)
        self.assertEqual(reset.json(), unknown.json())
        self.assertNotIn(delivered_token, reset.text)
        self.assertEqual(reset.headers["cache-control"], "no-store")

        updated = self.client.post(
            "/api/v1/auth/reset-password",
            json={
                "token": delivered_token,
                "new_password": _RESET_PASSWORD,
            },
        )
        repeated = self.client.post(
            "/api/v1/auth/reset-password",
            json={
                "token": delivered_token,
                "new_password": _RESET_PASSWORD,
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.headers["cache-control"], "no-store")
        self.assertEqual(repeated.status_code, 400)
        with closing(self.store._connect()) as connection:
            session_states = connection.execute(
                """
                SELECT revoked_at FROM auth_sessions
                WHERE user_id = ?
                """,
                (self._stored_user("worker@example.com")["user_id"],),
            ).fetchall()
        self.assertTrue(session_states)
        self.assertTrue(all(row["revoked_at"] is not None for row in session_states))
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {previous_session}"},
            ).status_code,
            401,
        )
        login = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "worker@example.com",
                "password": _RESET_PASSWORD,
            },
        )
        self.assertEqual(login.status_code, 200)

    def test_password_change_revokes_other_sessions_and_rotates_current(self) -> None:
        self._register("worker@example.com")
        self.assertEqual(self._verify("worker@example.com").status_code, 200)
        first_token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        ).json()["access_token"]
        second_token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        ).json()["access_token"]

        headers = {"Authorization": f"Bearer {first_token}"}
        wrong_current = self.client.post(
            "/api/v1/auth/change-password",
            headers=headers,
            json={
                "current_password": "incorrect current password",
                "new_password": _RESET_PASSWORD,
            },
        )
        self.assertEqual(wrong_current.status_code, 400)

        changed = self.client.post(
            "/api/v1/auth/change-password",
            headers=headers,
            json={
                "current_password": _PASSWORD,
                "new_password": _RESET_PASSWORD,
            },
        )
        self.assertEqual(changed.status_code, 200)
        self.assertIn("access_token", changed.json())
        self.assertIn("Other sessions were signed out", changed.json()["message"])
        self.assertEqual(changed.headers["cache-control"], "no-store")
        for token in (first_token, second_token):
            self.assertEqual(
                self.client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                ).status_code,
                401,
            )
        current_token = changed.json()["access_token"]
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {current_token}"},
            ).status_code,
            200,
        )

        revoked = self.client.post(
            "/api/v1/auth/change-password",
            headers={"Authorization": f"Bearer {current_token}"},
            json={
                "current_password": _RESET_PASSWORD,
                "new_password": _RESET_PASSWORD_2,
                "revoke_other_sessions": True,
            },
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertIn("access_token", revoked.json())
        self.assertIn("this session remains active", revoked.json()["message"])
        for token in (first_token, second_token, current_token):
            self.assertEqual(
                self.client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                ).status_code,
                401,
            )
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me",
                headers={
                    "Authorization": f"Bearer {revoked.json()['access_token']}"
                },
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/login",
                json={
                    "email": "worker@example.com",
                    "password": _RESET_PASSWORD_2,
                },
            ).status_code,
            200,
        )

    def test_active_sessions_are_private_and_revoke_one_session(self) -> None:
        self._register("worker@example.com")
        self.assertEqual(self._verify("worker@example.com").status_code, 200)
        self._register("other@example.com")
        self.assertEqual(self._verify("other@example.com").status_code, 200)
        worker_login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        ).json()
        self.assertNotIn("session_id", worker_login["user"])
        worker_token = worker_login["access_token"]
        other_token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "other@example.com", "password": _PASSWORD},
        ).json()["access_token"]
        worker_session_id = jwt.decode(
            worker_token,
            _JWT_SECRET,
            algorithms=["HS256"],
            issuer="telecom-network-troubleshooting-ai",
        )["sid"]
        other_session_id = jwt.decode(
            other_token,
            _JWT_SECRET,
            algorithms=["HS256"],
            issuer="telecom-network-troubleshooting-ai",
        )["sid"]
        worker_headers = {"Authorization": f"Bearer {worker_token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}

        worker_sessions = self.client.get(
            "/api/v1/auth/sessions",
            headers=worker_headers,
        )
        self.assertEqual(worker_sessions.status_code, 200)
        self.assertEqual(
            [item["session_id"] for item in worker_sessions.json()["sessions"]],
            [worker_session_id],
        )
        listed = worker_sessions.json()["sessions"][0]
        self.assertTrue(listed["is_current"])
        self.assertIn("login_at", listed)
        self.assertIn("last_activity_at", listed)
        self.assertNotIn(worker_token, worker_sessions.text)
        self.assertNotIn(
            "session_id",
            self.client.get(
                "/api/v1/auth/me",
                headers=worker_headers,
            ).json(),
        )

        cross_user_revoke = self.client.delete(
            f"/api/v1/auth/sessions/{other_session_id}",
            headers=worker_headers,
        )
        self.assertEqual(cross_user_revoke.status_code, 404)
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me",
                headers=other_headers,
            ).status_code,
            200,
        )

        revoked = self.client.delete(
            f"/api/v1/auth/sessions/{worker_session_id}",
            headers=worker_headers,
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertTrue(revoked.json()["current_session_revoked"])
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me",
                headers=worker_headers,
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me",
                headers=other_headers,
            ).status_code,
            200,
        )

    def test_logout_all_devices_revokes_current_and_persists_across_restart(self) -> None:
        self._register("worker@example.com")
        self.assertEqual(self._verify("worker@example.com").status_code, 200)
        tokens = [
            self.client.post(
                "/api/v1/auth/login",
                json={"email": "worker@example.com", "password": _PASSWORD},
            ).json()["access_token"]
            for _ in range(2)
        ]
        revoked = self.client.post(
            "/api/v1/auth/logout-all-devices",
            headers={"Authorization": f"Bearer {tokens[0]}"},
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertIn("including this one", revoked.json()["message"])
        self.assertEqual(revoked.headers["cache-control"], "no-store")
        for token in tokens:
            self.assertEqual(
                self.client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                ).status_code,
                401,
            )

        self.auth = AuthenticationService(
            ChatSessionStore(str(self.store.database_path))
        )
        self.app.dependency_overrides[get_auth_service] = lambda: self.auth
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {tokens[0]}"},
            ).status_code,
            401,
        )
        fresh_login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        )
        self.assertEqual(fresh_login.status_code, 200)
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me",
                headers={
                    "Authorization": (
                        f"Bearer {fresh_login.json()['access_token']}"
                    )
                },
            ).status_code,
            200,
        )

    def test_profile_email_change_verification_preserves_role_and_revokes_token(self) -> None:
        self._register("worker@example.com")
        self.assertEqual(self._verify("worker@example.com").status_code, 200)
        self._register("taken@example.com")
        self.assertTrue(self.store.promote_user_to_admin("worker@example.com"))
        access_token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        updated_profile = self.client.patch(
            "/api/v1/auth/profile",
            headers=headers,
            json={"name": "  Network Admin  "},
        )
        self.assertEqual(updated_profile.status_code, 200)
        self.assertEqual(updated_profile.json()["name"], "Network Admin")
        self.assertEqual(updated_profile.json()["role"], "admin")

        wrong_password = self.client.post(
            "/api/v1/auth/request-email-change",
            headers=headers,
            json={
                "current_password": "incorrect current password",
                "new_email": "new-worker@example.com",
            },
        )
        self.assertEqual(wrong_password.status_code, 400)
        duplicate = self.client.post(
            "/api/v1/auth/request-email-change",
            headers=headers,
            json={
                "current_password": _PASSWORD,
                "new_email": "taken@example.com",
            },
        )
        self.assertEqual(duplicate.status_code, 409)

        requested = self.client.post(
            "/api/v1/auth/request-email-change",
            headers=headers,
            json={
                "current_password": _PASSWORD,
                "new_email": "new-worker@example.com",
            },
        )
        self.assertEqual(requested.status_code, 202)
        self.assertNotIn(self.email_service.sent[-1][1], requested.text)
        self.assertEqual(
            self.store.get_user_by_email("worker@example.com")["email"],
            "worker@example.com",
        )
        change_token = self.email_service.sent[-1][1]

        verified = self.client.post(
            "/api/v1/auth/verify-email-change",
            json={"token": change_token},
        )
        repeated = self.client.post(
            "/api/v1/auth/verify-email-change",
            json={"token": change_token},
        )
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(repeated.status_code, 400)
        with closing(self.store._connect()) as connection:
            unrevoked_sessions = connection.execute(
                """
                SELECT COUNT(*) AS count FROM auth_sessions
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (self._stored_user("new-worker@example.com")["user_id"],),
            ).fetchone()["count"]
        self.assertEqual(unrevoked_sessions, 0)
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers=headers).status_code,
            401,
        )
        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "new-worker@example.com", "password": _PASSWORD},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["user"]["name"], "Network Admin")
        self.assertEqual(login.json()["user"]["role"], "admin")

    def test_account_deletion_requires_confirmation_and_preserves_other_users(self) -> None:
        self._register("worker@example.com")
        self.assertEqual(self._verify("worker@example.com").status_code, 200)
        self._register("other@example.com")
        self.assertEqual(self._verify("other@example.com").status_code, 200)
        worker = self._stored_user("worker@example.com")
        other = self._stored_user("other@example.com")
        access_token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        worker_session = str(uuid.uuid4())
        worker_message = self.store.save_exchange(
            worker_session,
            worker["user_id"],
            "Worker question",
            "Worker answer",
            [],
        )
        self.store.submit_feedback(
            worker_session,
            worker["user_id"],
            worker_message,
            "helpful",
            "Owned feedback",
        )
        other_session = str(uuid.uuid4())
        other_message = self.store.save_exchange(
            other_session,
            other["user_id"],
            "Other question",
            "Other answer",
            [],
        )
        self.store.submit_feedback(
            other_session,
            other["user_id"],
            other_message,
            "helpful",
            "Unrelated feedback",
        )

        missing_confirmation = self.client.request(
            "DELETE",
            "/api/v1/auth/account",
            headers=headers,
            json={"current_password": _PASSWORD, "confirmation": "DELETE"},
        )
        self.assertEqual(missing_confirmation.status_code, 422)
        wrong_password = self.client.request(
            "DELETE",
            "/api/v1/auth/account",
            headers=headers,
            json={
                "current_password": "incorrect current password",
                "confirmation": "DELETE MY ACCOUNT",
            },
        )
        self.assertEqual(wrong_password.status_code, 400)
        self.assertIsNotNone(self.store.get_user_by_id(worker["user_id"]))

        deleted = self.client.request(
            "DELETE",
            "/api/v1/auth/account",
            headers=headers,
            json={
                "current_password": _PASSWORD,
                "confirmation": "DELETE MY ACCOUNT",
            },
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertIsNone(self.store.get_user_by_id(worker["user_id"]))
        with closing(self.store._connect()) as connection:
            remaining_sessions = connection.execute(
                "SELECT COUNT(*) AS count FROM auth_sessions WHERE user_id = ?",
                (worker["user_id"],),
            ).fetchone()["count"]
            deleted_event = connection.execute(
                """
                SELECT user_id, metadata_json FROM auth_audit_events
                WHERE event_type = 'account_deleted'
                ORDER BY event_id DESC LIMIT 1
                """
            ).fetchone()
        self.assertEqual(remaining_sessions, 0)
        self.assertIsNone(deleted_event["user_id"])
        self.assertEqual(
            json.loads(deleted_event["metadata_json"]),
            {"deletion_scope": "completed"},
        )
        self.assertIsNone(self.store.get_session(worker_session, worker["user_id"]))
        self.assertIsNotNone(self.store.get_session(other_session, other["user_id"]))
        with closing(self.store._connect()) as connection:
            feedback_rows = connection.execute(
                "SELECT comment FROM response_feedback"
            ).fetchall()
        self.assertEqual([row["comment"] for row in feedback_rows], ["Unrelated feedback"])
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers=headers).status_code,
            401,
        )

    def test_password_reset_rate_limit_suppresses_delivery_without_disclosure(self) -> None:
        self.email_limiter = VerificationEmailRateLimiter(
            ip_max_per_hour=10,
            address_max_per_hour=2,
        )
        self.app.dependency_overrides[get_verification_email_limiter] = (
            lambda: self.email_limiter
        )
        self._register("worker@example.com")
        self.assertEqual(self._verify("worker@example.com").status_code, 200)
        payload = {"email": "worker@example.com"}
        first = self.client.post("/api/v1/auth/request-password-reset", json=payload)
        sent_after_first = len(self.email_service.sent)
        repeated = self.client.post(
            "/api/v1/auth/request-password-reset",
            json=payload,
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json(), repeated.json())
        self.assertEqual(sent_after_first, 2)
        self.assertEqual(len(self.email_service.sent), sent_after_first)

    def test_verification_email_rate_limit_suppresses_delivery_safely(self) -> None:
        self.verification_limiter = VerificationEmailRateLimiter(
            ip_max_per_hour=10,
            address_max_per_hour=1,
        )
        self.app.dependency_overrides[get_verification_email_limiter] = (
            lambda: self.verification_limiter
        )
        self._register("worker@example.com")
        initial_sent = len(self.email_service.sent)

        resend = self.client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "worker@example.com"},
        )
        self.assertEqual(resend.status_code, 202)
        self.assertEqual(len(self.email_service.sent), initial_sent)

    def test_registration_fails_safely_when_smtp_is_unconfigured(self) -> None:
        self.app.dependency_overrides[get_email_service] = (
            lambda: UnconfiguredEmailService()
        )
        response = self._register("worker@example.com")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Email verification is not configured.",
        )
        self.assertIsNone(self._stored_user("worker@example.com"))

    def test_resend_fails_safely_when_authentication_is_unconfigured(self) -> None:
        self._register("worker@example.com")
        sent_before = len(self.email_service.sent)
        with patch.object(settings, "JWT_SECRET_KEY", ""):
            response = self.client.post(
                "/api/v1/auth/resend-verification",
                json={"email": "worker@example.com"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Authentication is not configured.",
        )
        self.assertEqual(len(self.email_service.sent), sent_before)

    def test_protected_chat_history_and_admin_feedback_routes_enforce_roles(self) -> None:
        self.assertEqual(self._register("worker@example.com").status_code, 202)
        self.assertEqual(self._register("admin@example.com").status_code, 202)
        self.assertEqual(self._verify("worker@example.com").status_code, 200)
        self.assertEqual(self._verify("admin@example.com").status_code, 200)
        user = self._stored_user("worker@example.com")
        admin = self._stored_user("admin@example.com")
        self.assertEqual(admin["role"], "user")
        self.store.promote_user_to_admin("admin@example.com")
        user_login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        )
        user_token = user_login.json()["access_token"]
        admin_token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": _PASSWORD},
        ).json()["access_token"]
        user_session_id = str(uuid.uuid4())
        admin_session_id = str(uuid.uuid4())
        self.store.save_exchange(
            user_session_id, user["user_id"], "User question", "User answer", []
        )
        self.store.save_exchange(
            admin_session_id, admin["user_id"], "Admin question", "Admin answer", []
        )
        user_message_id = self.store.get_messages(
            user_session_id, user["user_id"]
        )[1]["message_id"]

        self.assertNotIn("password_hash", user_login.text)
        self.assertEqual(user["role"], "user")
        self.assertEqual(admin["role"], "user")
        self.assertEqual(
            self.client.get("/api/v1/chat/sessions").status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/feedback/summary",
                headers={"Authorization": f"Bearer {user_token}"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/ingest",
                headers={"Authorization": f"Bearer {user_token}"},
                json={},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/ingest/upload",
                headers={"Authorization": f"Bearer {user_token}"},
                files=[("files", ("manual.pdf", b"%PDF-1.7", "application/pdf"))],
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.request(
                "DELETE",
                "/api/v1/vectordb",
                headers={"Authorization": f"Bearer {user_token}"},
                json={"confirm": True},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(
                f"/api/v1/documents/{'a' * 64}",
                headers={"Authorization": f"Bearer {user_token}"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/feedback/summary",
                headers={"Authorization": f"Bearer {admin_token}"},
            ).status_code,
            200,
        )
        user_sessions = self.client.get(
            "/api/v1/chat/sessions",
            headers={"Authorization": f"Bearer {user_token}"},
        ).json()["sessions"]
        self.assertEqual(
            [item["session_id"] for item in user_sessions],
            [user_session_id],
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/chat/sessions/{admin_session_id}",
                headers={"Authorization": f"Bearer {user_token}"},
            ).status_code,
            404,
        )
        submitted_feedback = self.client.post(
            "/api/v1/feedback",
            headers={"Authorization": f"Bearer {user_token}"},
            json={
                "session_id": user_session_id,
                "message_id": user_message_id,
                "rating": "helpful",
            },
        )
        self.assertEqual(submitted_feedback.status_code, 200)
        admin_summary = self.client.get(
            "/api/v1/feedback/summary",
            headers={"Authorization": f"Bearer {admin_token}"},
        ).json()
        self.assertEqual(admin_summary["total_feedback"], 1)
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {user_token}"},
            ).json()["user_id"],
            user["user_id"],
        )

    def test_admin_pdf_upload_is_persisted_under_data_dir_before_ingestion(self) -> None:
        self.assertEqual(self._register("admin@example.com").status_code, 202)
        self.assertEqual(self._verify("admin@example.com").status_code, 200)
        self.store.promote_user_to_admin("admin@example.com")
        token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": _PASSWORD},
        ).json()["access_token"]
        ingested = {}

        async def ingest_documents(file_paths):
            ingested["paths"] = file_paths
            ingested["contents"] = [
                Path(file_path).read_bytes() for file_path in file_paths
            ]
            return {
                "status": "success",
                "documents_processed": len(file_paths),
                "chunks_created": 2,
            }

        self.chat_service.ingest_documents = ingest_documents
        with patch.object(settings, "DATA_DIR", self.temp_dir.name):
            response = self.client.post(
                "/api/v1/ingest/upload",
                headers={"Authorization": f"Bearer {token}"},
                files=[
                    ("files", ("../../manual.pdf", b"%PDF-1.7 first", "application/pdf")),
                    ("files", ("manual.pdf", b"%PDF-1.7 second", "application/pdf")),
                ],
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["documents_processed"], 2)
        self.assertEqual(ingested["contents"], [b"%PDF-1.7 first", b"%PDF-1.7 second"])
        self.assertEqual(
            [Path(file_path).name for file_path in ingested["paths"]],
            ["manual.pdf", "manual.pdf"],
        )
        upload_root = (Path(self.temp_dir.name) / "uploads").resolve()
        self.assertTrue(
            all(
                Path(file_path).resolve().is_relative_to(upload_root)
                for file_path in ingested["paths"]
            )
        )
        self.assertNotIn("../../manual.pdf", response.text)

    def test_pdf_upload_rejects_non_pdf_and_oversized_files_without_retaining_partial_data(self) -> None:
        self.assertEqual(self._register("admin@example.com").status_code, 202)
        self.assertEqual(self._verify("admin@example.com").status_code, 200)
        self.store.promote_user_to_admin("admin@example.com")
        token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": _PASSWORD},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        with patch.object(settings, "DATA_DIR", self.temp_dir.name):
            invalid_type = self.client.post(
                "/api/v1/ingest/upload",
                headers=headers,
                files=[("files", ("notes.txt", b"not a PDF", "text/plain"))],
            )
            self.assertEqual(invalid_type.status_code, 422)

            with patch("backend.api.routes._MAX_UPLOAD_BYTES", 3):
                oversized = self.client.post(
                    "/api/v1/ingest/upload",
                    headers=headers,
                    files=[("files", ("manual.pdf", b"1234", "application/pdf"))],
                )

        self.assertEqual(oversized.status_code, 413)
        upload_root = Path(self.temp_dir.name) / "uploads"
        self.assertFalse(upload_root.exists() and any(upload_root.iterdir()))

    def test_security_activity_is_user_scoped_and_omits_identifier_hash(self) -> None:
        self.assertEqual(self._register("worker@example.com").status_code, 202)
        self.assertEqual(self._register("other@example.com").status_code, 202)
        self.assertEqual(self._verify("worker@example.com").status_code, 200)
        self.assertEqual(self._verify("other@example.com").status_code, 200)
        worker = self._stored_user("worker@example.com")
        other = self._stored_user("other@example.com")
        self.store.record_audit_event(
            "login_failed",
            {
                "source": "api",
                "reason_code": "invalid_credentials",
                "identifier_hash": "a" * 64,
            },
            worker["user_id"],
        )
        self.store.record_audit_event(
            "password_changed",
            {"source": "web"},
            other["user_id"],
        )
        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        )
        token = login.json()["access_token"]

        response = self.client.get(
            "/api/v1/auth/activity?limit=10",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 200)
        events = response.json()["events"]
        event_types = [event["event_type"] for event in events]
        self.assertIn("login_failed", event_types)
        self.assertIn("login_success", event_types)
        self.assertNotIn("password_changed", event_types)
        self.assertTrue(
            all(
                "identifier_hash" not in event["metadata"]
                for event in events
            )
        )
        self.assertEqual(
            self.client.get("/api/v1/auth/activity?limit=101", headers={
                "Authorization": f"Bearer {token}",
            }).status_code,
            422,
        )

    def test_search_parameters_are_bounded(self) -> None:
        self.assertEqual(self._register("worker@example.com").status_code, 202)
        self.assertEqual(self._verify("worker@example.com").status_code, 200)
        token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        self.assertEqual(
            self.client.get(
                "/api/v1/search",
                params={"q": "link", "top_k": 21},
                headers=headers,
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/search",
                params={"q": "x" * 1001},
                headers=headers,
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/search",
                params={"q": "link", "similarity_threshold": 1.1},
                headers=headers,
            ).status_code,
            422,
        )

    def test_document_deletion_requires_admin_and_reports_backend_failure(self) -> None:
        self.assertEqual(self._register("admin@example.com").status_code, 202)
        self.assertEqual(self._verify("admin@example.com").status_code, 200)
        self.store.promote_user_to_admin("admin@example.com")
        token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": _PASSWORD},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        document_id = "d" * 64
        self.chat_service.delete_document = lambda supplied_id: (
            3 if supplied_id == document_id else 0
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/ingest",
                headers=headers,
                json={"file_paths": ["C:\\Windows\\win.ini"]},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/ingest",
                headers=headers,
                json={
                    "file_paths": [],
                    "directory": settings.DATA_DIR,
                },
            ).status_code,
            422,
        )

        deleted = self.client.delete(
            f"/api/v1/documents/{document_id}",
            headers=headers,
        )

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["chunks_deleted"], 3)
        self.assertEqual(
            deleted.json()["status"],
            "success",
        )

        async def failed_collection_delete(confirm: bool = False):
            return {"status": "error", "message": "internal Chroma details"}

        self.chat_service.delete_vector_database = failed_collection_delete
        response = self.client.request(
            "DELETE",
            "/api/v1/vectordb",
            headers=headers,
            json={"confirm": True},
        )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("internal Chroma details", response.text)


    def test_invalid_credentials_duplicate_registration_and_expired_tokens_are_safe(self) -> None:
        registered = self._register("worker@example.com")
        duplicate = self._register("WORKER@example.com")
        invalid_login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": "wrong password"},
        )
        self.assertEqual(registered.status_code, 202)
        self.assertEqual(duplicate.status_code, 202)
        self.assertEqual(registered.json(), duplicate.json())
        self.assertNotIn("SQL", duplicate.text)
        self.assertEqual(invalid_login.status_code, 401)
        self.assertEqual(invalid_login.json()["detail"], "Invalid email or password.")
        self.assertEqual(self._verify("worker@example.com").status_code, 200)
        successful_login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        )
        self.assertEqual(successful_login.status_code, 200)
        self.assertEqual(successful_login.headers["cache-control"], "no-store")

        expired = jwt.encode(
            {
                "sub": self._stored_user("worker@example.com")["user_id"],
                "iss": "telecom-network-troubleshooting-ai",
                "iat": 1,
                "exp": 2,
            },
            _JWT_SECRET,
            algorithm="HS256",
        )
        response = self.client.get(
            "/api/v1/chat/sessions",
            headers={"Authorization": f"Bearer {expired}"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid or expired access token.")

    def test_public_registration_rejects_client_supplied_roles(self) -> None:
        response = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "worker@example.com",
                "password": _PASSWORD,
                "role": "admin",
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_public_email_claim_cannot_access_admin_routes(self) -> None:
        registered = self._register("admin@example.com")
        self.assertEqual(registered.status_code, 202)
        self.assertEqual(self._verify("admin@example.com").status_code, 200)
        token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": _PASSWORD},
        ).json()["access_token"]

        self.assertEqual(self._stored_user("admin@example.com")["role"], "user")
        self.assertEqual(
            self.client.get(
                "/api/v1/feedback/summary",
                headers={"Authorization": f"Bearer {token}"},
            ).status_code,
            403,
        )

    def test_login_lockout_is_generic_and_expires_for_legitimate_user(self) -> None:
        clock = FakeClock()
        limiter = LoginRateLimiter(
            ip_max_failures=10,
            account_max_failures=2,
            failure_window_seconds=60,
            account_lockout_seconds=30,
            clock=clock,
        )
        self.app.dependency_overrides[get_login_rate_limiter] = lambda: limiter
        self._register("worker@example.com")
        self._verify("worker@example.com")

        wrong_password = {
            "email": "worker@example.com",
            "password": "incorrect password",
        }
        unknown_account = {
            "email": "unknown@example.com",
            "password": "incorrect password",
        }
        known_failure = self.client.post("/api/v1/auth/login", json=wrong_password)
        unknown_failure = self.client.post("/api/v1/auth/login", json=unknown_account)
        self.assertEqual(known_failure.status_code, 401)
        self.assertEqual(unknown_failure.status_code, 401)
        self.assertEqual(
            known_failure.json()["detail"],
            unknown_failure.json()["detail"],
        )
        self.assertEqual(
            self.client.post("/api/v1/auth/login", json=wrong_password).status_code,
            401,
        )
        unknown_second_failure = self.client.post(
            "/api/v1/auth/login",
            json=unknown_account,
        )
        self.assertEqual(unknown_second_failure.status_code, 401)
        blocked = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(
            blocked.json()["detail"],
            "Login temporarily unavailable. Please try again later.",
        )
        unknown_blocked = self.client.post(
            "/api/v1/auth/login",
            json=unknown_account,
        )
        self.assertEqual(unknown_blocked.status_code, 429)
        self.assertEqual(
            unknown_blocked.json()["detail"],
            blocked.json()["detail"],
        )

        clock.value += 31
        recovered = self.client.post(
            "/api/v1/auth/login",
            json={"email": "worker@example.com", "password": _PASSWORD},
        )
        self.assertEqual(recovered.status_code, 200)


class DeploymentSecurityTests(unittest.TestCase):
    def test_production_requires_https_origins_and_a_strong_jwt_secret(self) -> None:
        with self.assertRaises(ValueError):
            validate_deployment_configuration(
                "production",
                ["http://localhost:8501"],
                _JWT_SECRET,
                _AUDIT_SECRET,
            )
        with self.assertRaises(ValueError):
            validate_deployment_configuration(
                "production",
                ["https://*.example.com"],
                _JWT_SECRET,
                _AUDIT_SECRET,
            )
        with self.assertRaises(ValueError):
            validate_deployment_configuration(
                "production",
                ["https://app.example.com"],
                "",
                _AUDIT_SECRET,
            )
        with self.assertRaises(ValueError):
            validate_deployment_configuration(
                "production",
                ["https://app.example.com"],
                _JWT_SECRET,
                "",
            )
        with self.assertRaises(ValueError):
            validate_deployment_configuration(
                "production",
                ["https://app.example.com"],
                _JWT_SECRET,
                _JWT_SECRET,
            )

        validate_deployment_configuration(
            "production",
            ["https://app.example.com"],
            _JWT_SECRET,
            _AUDIT_SECRET,
        )

    def test_development_localhost_origins_remain_valid(self) -> None:
        validate_deployment_configuration(
            "development",
            ["http://localhost:8501", "http://127.0.0.1:8501"],
            "",
            "",
        )

    def test_application_rejects_unsafe_production_cors_and_disables_credentials(
        self,
    ) -> None:
        with patch.multiple(
            settings,
            ENVIRONMENT="production",
            CORS_ORIGINS=["http://localhost:8501"],
            JWT_SECRET_KEY=_JWT_SECRET,
            AUDIT_IDENTIFIER_HMAC_SECRET=SecretStr(_AUDIT_SECRET),
        ):
            with self.assertRaises(ValueError):
                create_app()

        with patch.multiple(
            settings,
            ENVIRONMENT="production",
            CORS_ORIGINS=["https://assistant.example.com"],
            JWT_SECRET_KEY=_JWT_SECRET,
            AUDIT_IDENTIFIER_HMAC_SECRET=SecretStr(_AUDIT_SECRET),
        ):
            app = create_app()
        cors = next(
            middleware
            for middleware in app.user_middleware
            if middleware.cls.__name__ == "CORSMiddleware"
        )
        self.assertFalse(cors.kwargs["allow_credentials"])

    def test_limiter_throttles_ip_and_expires_sliding_window(self) -> None:
        clock = FakeClock()
        limiter = LoginRateLimiter(
            ip_max_failures=2,
            account_max_failures=10,
            failure_window_seconds=60,
            account_lockout_seconds=30,
            clock=clock,
        )
        limiter.record_failure("192.0.2.1", "first@example.com")
        limiter.record_failure("192.0.2.1", "second@example.com")
        with self.assertRaises(LoginRateLimitExceeded):
            limiter.check("192.0.2.1", "third@example.com")

        clock.value += 61
        limiter.check("192.0.2.1", "third@example.com")


class AuthenticationUiTests(unittest.TestCase):
    def test_login_screen_gates_pages_and_keeps_token_in_session_state(self) -> None:
        app = AppTest.from_file(str(_ENTRYPOINT), default_timeout=60)
        auth_result = {
            "access_token": "temporary-test-token",
            "user": {
                "user_id": "user-1",
                "email": "worker@example.com",
                "role": "user",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        }
        with (
            patch(
                "frontend.pages.authentication.login_user",
                return_value=auth_result,
            ),
            patch(
                "frontend.components.sidebar.list_chat_sessions",
                return_value={"sessions": []},
            ),
            patch(
                "frontend.pages.overview.health_check",
                return_value={"status": "degraded"},
            ),
        ):
            app.run()
            self.assertTrue(app.text_input(key="auth_login_email"))
            app.text_input(key="auth_login_email").set_value("worker@example.com").run()
            app.text_input(key="auth_login_password").set_value(_PASSWORD).run()
            app.form_submit_button(key="auth_login_submit").click().run()

        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["access_token"], "temporary-test-token")
        self.assertEqual(app.session_state["current_user"]["role"], "user")
        self.assertEqual(app.session_state["auth_login_password"], "")
        self.assertNotIn(
            "feedback_dashboard",
            [button.key.removeprefix("nav_") for button in app.button if button.key.startswith("nav_")],
        )

    def test_registration_and_resend_show_verification_status(self) -> None:
        app = AppTest.from_file(str(_ENTRYPOINT), default_timeout=60)
        with (
            patch(
                "frontend.pages.authentication.register_user",
                return_value={
                    "message": "Check your email for verification instructions."
                },
            ) as register,
            patch(
                "frontend.pages.authentication.resend_verification",
                return_value={
                    "message": "If this address is eligible, instructions will be sent."
                },
            ) as resend,
        ):
            app.run()
            app.text_input(key="auth_register_name").set_value("Test User")
            app.text_input(key="auth_register_email").set_value("test@example.com")
            app.text_input(key="auth_register_password").set_value(_PASSWORD)
            app.form_submit_button(key="auth_register_submit").click().run()

            self.assertFalse(app.exception)
            register.assert_called_once_with("Test User", "test@example.com", _PASSWORD)
            self.assertEqual(app.session_state["auth_register_password"], "")
            self.assertEqual(
                app.text_input(key="auth_resend_email").value,
                "test@example.com",
            )
            app.form_submit_button(
                key="auth_resend_verification_submit"
            ).click().run()

        resend.assert_called_once_with("test@example.com")
        self.assertTrue(
            any(
                "instructions will be sent" in element.value
                for element in app.success
            )
        )

    def test_verification_link_routes_to_authentication_when_already_signed_in(self) -> None:
        app = AppTest.from_file(str(_ENTRYPOINT), default_timeout=60)
        app.session_state["access_token"] = "test-access-token"
        app.session_state["current_user"] = {
            "user_id": "user-1",
            "email": "test@example.com",
            "role": "user",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        app.query_params["verify_email"] = "one-time-test-token"
        with (
            patch(
                "frontend.pages.authentication.verify_email",
                return_value={"message": "Email verified. You can now sign in."},
            ) as verify,
            patch(
                "frontend.components.sidebar.list_chat_sessions",
                return_value={"sessions": []},
            ),
        ):
            app.run()

        self.assertFalse(app.exception)
        verify.assert_called_once_with("one-time-test-token")
        self.assertNotIn("verify_email", app.query_params)
        self.assertTrue(any("Email verified" in item.value for item in app.success))

    def test_email_change_link_revokes_the_signed_in_frontend_session(self) -> None:
        app = AppTest.from_file(str(_ENTRYPOINT), default_timeout=60)
        app.session_state["access_token"] = "old-session-token"
        app.session_state["current_user"] = {
            "user_id": "user-1",
            "email": "worker@example.com",
            "role": "user",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        app.query_params["verify_email_change"] = "email-change-token"
        with (
            patch(
                "frontend.pages.authentication.verify_email_change",
                return_value={
                    "message": "Email updated. Sign in again with your new address."
                },
            ) as verify,
            patch(
                "frontend.components.sidebar.list_chat_sessions",
                return_value={"sessions": []},
            ),
        ):
            app.run()

        self.assertFalse(app.exception)
        verify.assert_called_once_with("email-change-token")
        self.assertNotIn("verify_email_change", app.query_params)
        self.assertIsNone(app.session_state["access_token"])
        self.assertIsNone(app.session_state["current_user"])
        self.assertTrue(any("Email updated" in item.value for item in app.success))

    def test_forgot_password_option_requests_a_generic_reset_message(self) -> None:
        app = AppTest.from_file(str(_ENTRYPOINT), default_timeout=60)
        with patch(
            "frontend.pages.authentication.request_password_reset",
            return_value={
                "message": (
                    "If an account exists for this address, password reset "
                    "instructions will be sent."
                )
            },
        ) as request_reset:
            app.run()
            app.button(key="auth_forgot_password").click().run()
            app.text_input(key="auth_password_reset_email").set_value(
                "worker@example.com"
            )
            app.form_submit_button(
                key="auth_password_reset_request_submit"
            ).click().run()

        self.assertFalse(app.exception)
        request_reset.assert_called_once_with("worker@example.com")
        self.assertTrue(
            any("If an account exists" in item.value for item in app.success)
        )

    def test_password_reset_page_confirms_and_submits_the_new_password(self) -> None:
        app = AppTest.from_file(str(_ENTRYPOINT), default_timeout=60)
        app.query_params["reset_password"] = "reset-token"
        with patch(
            "frontend.pages.password_reset.reset_password",
            return_value={"message": "Password updated. You can now sign in."},
        ) as complete_reset:
            app.run()
            self.assertTrue(app.text_input(key="password_reset_new_password"))
            app.text_input(key="password_reset_new_password").set_value(
                _RESET_PASSWORD
            )
            app.text_input(key="password_reset_confirmation").set_value(
                _RESET_PASSWORD
            )
            app.form_submit_button(key="password_reset_submit").click().run()

        self.assertFalse(app.exception)
        complete_reset.assert_called_once_with("reset-token", _RESET_PASSWORD)
        self.assertNotIn("reset_password", app.query_params)
        self.assertTrue(
            any("Your password has been updated" in item.value for item in app.success)
        )


class ApiClientTests(unittest.TestCase):
    def test_patch_requests_send_json_and_bearer_headers(self) -> None:
        from unittest.mock import Mock

        response = Mock()
        response.json.return_value = {"name": "Updated"}
        with (
            patch(
                "frontend.components.api_client._request_headers",
                return_value={"Authorization": "Bearer test-token"},
            ),
            patch(
                "frontend.components.api_client.requests.patch",
                return_value=response,
            ) as patch_request,
        ):
            result = call_api(
                "/auth/profile",
                "PATCH",
                {"name": "Updated"},
                timeout=15,
            )

        self.assertEqual(result, {"name": "Updated"})
        patch_request.assert_called_once_with(
            f"{API_BASE_URL}/auth/profile",
            json={"name": "Updated"},
            headers={"Authorization": "Bearer test-token"},
            timeout=15,
        )


class AccountSecurityUiTests(unittest.TestCase):
    def test_settings_change_password_and_confirm_logout_all(self) -> None:
        app = AppTest.from_file(str(_ENTRYPOINT), default_timeout=60)
        app.session_state["access_token"] = "old-session-token"
        app.session_state["current_user"] = {
            "user_id": "user-1",
            "email": "worker@example.com",
            "role": "user",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        app.session_state["current_page"] = "settings"
        with (
            patch(
                "frontend.components.sidebar.list_chat_sessions",
                return_value={"sessions": []},
            ),
            patch(
                "frontend.pages.settings.get_health_status",
                return_value={"llm_model": "test", "embedding_model": "test"},
            ),
            patch(
                "frontend.pages.settings.get_groq_key_status",
                return_value={"label": "Configured", "details": ""},
            ),
            patch(
                "frontend.pages.settings.get_vector_db_status",
                return_value={"label": "Ready", "indexed_chunks": 0},
            ),
            patch(
                "frontend.pages.settings.change_password",
                return_value={
                    "message": "Password changed.",
                    "access_token": "rotated-session-token",
                },
            ) as change_password,
            patch(
                "frontend.pages.settings.list_active_sessions",
                return_value={"sessions": []},
            ),
            patch(
                "frontend.pages.settings.get_security_activity",
                return_value={"events": []},
            ),
            patch(
                "frontend.pages.settings.logout_all_devices",
                return_value={"message": "All sessions revoked."},
            ) as logout_all,
        ):
            app.run()
            app.text_input(key="settings_current_password").set_value(_PASSWORD)
            app.text_input(key="settings_new_password").set_value(_RESET_PASSWORD)
            app.text_input(key="settings_confirm_password").set_value(
                _RESET_PASSWORD
            )
            app.form_submit_button(
                key="settings_change_password_submit"
            ).click().run()

            change_password.assert_called_once_with(
                _PASSWORD,
                _RESET_PASSWORD,
                True,
            )
            self.assertEqual(
                app.session_state["access_token"],
                "rotated-session-token",
            )
            self.assertTrue(
                any("Password changed." in item.value for item in app.success)
            )

            app.button(key="settings_logout_all_devices").click().run()
            self.assertTrue(
                any("including this current session" in item.value for item in app.warning)
            )
            app.button(key="settings_confirm_logout_all").click().run()

        logout_all.assert_called_once_with()
        self.assertFalse(app.exception)
        self.assertIsNone(app.session_state["access_token"])
        self.assertIn("All active sessions", app.session_state["auth_success"])

    def test_settings_lists_and_revokes_individual_sessions(self) -> None:
        app = AppTest.from_file(str(_ENTRYPOINT), default_timeout=60)
        current_session_id = "c" * 40
        other_session_id = "o" * 40
        app.session_state["access_token"] = "current-jwt"
        app.session_state["current_user"] = {
            "user_id": "user-1",
            "name": "Worker",
            "email": "worker@example.com",
            "role": "user",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        app.session_state["current_page"] = "settings"
        sessions = [
            {
                "session_id": current_session_id,
                "login_at": "2026-11-27T00:00:00+00:00",
                "last_activity_at": "2026-11-27T00:10:00+00:00",
                "expires_at": "2026-11-27T01:00:00+00:00",
                "user_agent_family": "Chrome",
                "ip_masked": "192.0.2.0/24",
                "is_current": True,
            },
            {
                "session_id": other_session_id,
                "login_at": "2026-11-26T23:00:00+00:00",
                "last_activity_at": "2026-11-26T23:10:00+00:00",
                "expires_at": "2026-11-27T00:00:00+00:00",
                "user_agent_family": "Firefox",
                "ip_masked": "192.0.2.0/24",
                "is_current": False,
            },
        ]
        with (
            patch(
                "frontend.components.sidebar.list_chat_sessions",
                return_value={"sessions": []},
            ),
            patch(
                "frontend.pages.settings.get_health_status",
                return_value={"llm_model": "test", "embedding_model": "test"},
            ),
            patch(
                "frontend.pages.settings.get_groq_key_status",
                return_value={"label": "Configured", "details": ""},
            ),
            patch(
                "frontend.pages.settings.get_vector_db_status",
                return_value={"label": "Ready", "indexed_chunks": 0},
            ),
            patch(
                "frontend.pages.settings.list_active_sessions",
                return_value={"sessions": sessions},
            ) as list_sessions,
            patch(
                "frontend.pages.settings.get_security_activity",
                return_value={"events": []},
            ),
            patch(
                "frontend.pages.settings.revoke_active_session",
                side_effect=[
                    {
                        "message": "Session revoked.",
                        "current_session_revoked": False,
                    },
                    {
                        "message": "This session is revoked. You are now signed out.",
                        "current_session_revoked": True,
                    },
                ],
            ) as revoke_session,
        ):
            app.run()
            self.assertTrue(
                any("This device (current session)" in item.value for item in app.markdown)
            )
            self.assertTrue(any("Device: Chrome" in item.value for item in app.text))
            app.button(
                key=f"settings_revoke_session_{other_session_id}"
            ).click().run()
            revoke_session.assert_called_once_with(other_session_id)
            self.assertEqual(app.session_state["access_token"], "current-jwt")

            app.button(
                key=f"settings_revoke_session_{current_session_id}"
            ).click().run()
            self.assertEqual(revoke_session.call_count, 2)

        self.assertEqual(list_sessions.call_count, 2)
        self.assertFalse(app.exception)
        self.assertIsNone(app.session_state["access_token"])
        self.assertEqual(
            app.session_state["auth_success"],
            "This session is revoked. You are now signed out.",
        )

    def test_settings_updates_profile_requests_email_and_confirms_deletion(self) -> None:
        app = AppTest.from_file(str(_ENTRYPOINT), default_timeout=60)
        app.session_state["access_token"] = "test-session-token"
        app.session_state["current_user"] = {
            "user_id": "user-1",
            "name": "Original Name",
            "email": "worker@example.com",
            "role": "admin",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        app.session_state["current_page"] = "settings"
        with (
            patch(
                "frontend.components.sidebar.list_chat_sessions",
                return_value={"sessions": []},
            ),
            patch(
                "frontend.pages.settings.get_health_status",
                return_value={"llm_model": "test", "embedding_model": "test"},
            ),
            patch(
                "frontend.pages.settings.get_groq_key_status",
                return_value={"label": "Configured", "details": ""},
            ),
            patch(
                "frontend.pages.settings.get_vector_db_status",
                return_value={"label": "Ready", "indexed_chunks": 0},
            ),
            patch(
                "frontend.pages.settings.list_active_sessions",
                return_value={"sessions": []},
            ),
            patch(
                "frontend.pages.settings.get_security_activity",
                return_value={"events": []},
            ),
            patch(
                "frontend.pages.settings.update_profile",
                return_value={
                    "name": "Updated Name",
                    "email": "worker@example.com",
                    "role": "admin",
                },
            ) as update_profile,
            patch(
                "frontend.pages.settings.request_email_change",
                return_value={
                    "message": "Check the new email for verification."
                },
            ) as request_email_change,
            patch(
                "frontend.pages.settings.delete_account",
                return_value={"message": "Account deleted."},
            ) as delete_account,
        ):
            app.run()
            app.text_input(key="settings_profile_name").set_value("Updated Name")
            app.form_submit_button(key="settings_profile_submit").click().run()
            update_profile.assert_called_once_with("Updated Name")
            self.assertEqual(
                app.session_state["current_user"]["name"],
                "Updated Name",
            )

            app.text_input(key="settings_new_email").set_value(
                "new-worker@example.com"
            )
            app.text_input(key="settings_email_current_password").set_value(
                _PASSWORD
            )
            app.form_submit_button(
                key="settings_email_change_submit"
            ).click().run()
            request_email_change.assert_called_once_with(
                _PASSWORD,
                "new-worker@example.com",
            )

            app.text_input(key="settings_delete_current_password").set_value(
                _PASSWORD
            )
            app.text_input(key="settings_delete_confirmation").set_value(
                "DELETE"
            )
            app.form_submit_button(
                key="settings_delete_account_submit"
            ).click().run()
            delete_account.assert_not_called()
            self.assertTrue(
                any("Type DELETE MY ACCOUNT" in item.value for item in app.error)
            )
            app.text_input(key="settings_delete_current_password").set_value(
                _PASSWORD
            )
            app.text_input(key="settings_delete_confirmation").set_value(
                "DELETE MY ACCOUNT"
            )
            app.form_submit_button(
                key="settings_delete_account_submit"
            ).click().run()
            delete_account.assert_called_once_with(
                _PASSWORD,
                "DELETE MY ACCOUNT",
            )
            self.assertIsNone(app.session_state["access_token"])
            self.assertIsNone(app.session_state["current_user"])
            self.assertEqual(
                app.session_state["auth_success"],
                "Account deleted.",
            )

    def test_settings_shows_safe_security_activity(self) -> None:
            app = AppTest.from_file(str(_ENTRYPOINT), default_timeout=60)
            app.session_state["access_token"] = "test-session-token"
            app.session_state["current_user"] = {
                "user_id": "user-1",
                "email": "worker@example.com",
                "role": "user",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            app.session_state["current_page"] = "settings"
            with (
                patch(
                    "frontend.components.sidebar.list_chat_sessions",
                    return_value={"sessions": []},
                ),
                patch(
                    "frontend.pages.settings.get_health_status",
                    return_value={"llm_model": "test", "embedding_model": "test"},
                ),
                patch(
                    "frontend.pages.settings.get_groq_key_status",
                    return_value={"label": "Configured", "details": ""},
                ),
                patch(
                    "frontend.pages.settings.get_vector_db_status",
                    return_value={"label": "Ready", "indexed_chunks": 0},
                ),
                patch(
                    "frontend.pages.settings.list_active_sessions",
                    return_value={"sessions": []},
                ),
                patch(
                    "frontend.pages.settings.get_security_activity",
                    return_value={
                        "events": [{
                            "event_type": "login_success",
                            "occurred_at": "2026-11-27T00:00:00+00:00",
                            "metadata": {
                                "source": "web",
                                "ip_masked": "192.0.2.0/24",
                                "user_agent_family": "Chrome",
                                "identifier_hash": "sensitive-hash",
                            },
                        }]
                    },
                ),
            ):
                app.run()

            rendered = "\n".join(
                str(element.value)
                for kind in ("markdown", "caption")
                for element in getattr(app, kind)
            )
            self.assertFalse(app.exception)
            self.assertIn("Successful sign-in", rendered)
            self.assertIn("192.0.2.0/24", rendered)
            self.assertNotIn("sensitive-hash", rendered)

    def test_admin_vector_database_deletion_requires_confirmation(self) -> None:
            app = AppTest.from_file(str(_ENTRYPOINT), default_timeout=60)
            app.session_state["access_token"] = "admin-session-token"
            app.session_state["current_user"] = {
                "user_id": "admin-1",
                "email": "admin@example.com",
                "role": "admin",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            app.session_state["current_page"] = "settings"
            with (
                patch(
                    "frontend.components.sidebar.list_chat_sessions",
                    return_value={"sessions": []},
                ),
                patch(
                    "frontend.pages.settings.get_health_status",
                    return_value={"llm_model": "test", "embedding_model": "test"},
                ),
                patch(
                    "frontend.pages.settings.get_groq_key_status",
                    return_value={"label": "Configured", "details": ""},
                ),
                patch(
                    "frontend.pages.settings.get_vector_db_status",
                    return_value={"label": "Ready", "indexed_chunks": 0},
                ),
                patch(
                    "frontend.pages.settings.list_active_sessions",
                    return_value={"sessions": []},
                ),
                patch(
                    "frontend.pages.settings.get_security_activity",
                    return_value={"events": []},
                ),
                patch(
                    "frontend.pages.settings.delete_vector_database",
                    return_value={
                        "status": "success",
                        "message": "Vector database collection deleted successfully.",
                    },
                ) as delete_database,
                patch("frontend.pages.settings.st.rerun"),
            ):
                app.run()
                app.button(key="settings_delete_vectordb").click().run()
                self.assertTrue(
                    any("permanently removes all indexed vectors" in item.value for item in app.warning)
                )
                app.button(key="settings_confirm_delete_vectordb").click().run()

            delete_database.assert_called_once_with(confirm=True)
            self.assertFalse(app.exception)


if __name__ == "__main__":
    unittest.main()
