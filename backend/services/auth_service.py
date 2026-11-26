"""Authentication services for user accounts and short-lived JWTs."""

import bcrypt
import hashlib
import jwt
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.config import settings
from backend.services.audit_events import (
    hash_login_identifier,
    mask_ip_address,
    normalize_user_agent_family,
)
from backend.services.session_store import ChatSessionStore


_TOKEN_ISSUER = "telecom-network-troubleshooting-ai"
_EMAIL_LOCAL_PART = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
_DOMAIN_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class AuthConfigurationError(RuntimeError):
    """Raised when authentication has not been configured safely."""


class EmailAlreadyRegisteredError(ValueError):
    """Raised when registration uses an existing email address."""


class InvalidCredentialsError(ValueError):
    """Raised when a login attempt has invalid credentials."""


class EmailNotVerifiedError(ValueError):
    """Raised when valid credentials belong to an unverified email address."""


class InvalidCurrentPasswordError(ValueError):
    """Raised when the supplied current password does not match the account."""


class PasswordReuseError(ValueError):
    """Raised when a new password matches the current password."""


class EmailAlreadyInUseError(ValueError):
    """Raised when a requested email address belongs to another account."""


def normalize_email(email: str) -> str:
    """Validate and normalize an email address used as an account identifier."""
    normalized = email.strip().lower()
    if len(normalized) > 254 or normalized.count("@") != 1:
        raise ValueError("Invalid email address.")

    local_part, domain = normalized.rsplit("@", 1)
    labels = domain.split(".")
    if (
        not local_part
        or len(local_part) > 64
        or local_part.startswith(".")
        or local_part.endswith(".")
        or ".." in local_part
        or not _EMAIL_LOCAL_PART.fullmatch(local_part)
        or len(labels) < 2
        or any(not _DOMAIN_LABEL.fullmatch(label) for label in labels)
    ):
        raise ValueError("Invalid email address.")
    return normalized


def validate_password(password: str) -> bytes:
    """Enforce bcrypt's input limit and a minimum password length."""
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        raise ValueError("Password must be no more than 72 UTF-8 bytes.")
    return password_bytes


def validate_new_password(password: str) -> bytes:
    """Validate a new password with length and character-class requirements."""
    password_bytes = validate_password(password)
    if not (
        any(character.islower() for character in password)
        and any(character.isupper() for character in password)
        and any(character.isdigit() for character in password)
        and any(not character.isalnum() for character in password)
    ):
        raise ValueError(
            "Password must include lowercase, uppercase, numeric, "
            "and special characters."
        )
    return password_bytes


def validate_name(name: str) -> str:
    """Normalize and validate a public account display name."""
    normalized = name.strip() if isinstance(name, str) else ""
    if not normalized or len(normalized) > 120 or any(
        ord(character) < 32 for character in normalized
    ):
        raise ValueError("Name must contain 1 to 120 printable characters.")
    return normalized


class AuthenticationService:
    """Manage account passwords, email verification, and JWT validation."""

    def __init__(self, session_store: ChatSessionStore) -> None:
        self.session_store = session_store
        self._dummy_password_hash: Optional[bytes] = None

    @staticmethod
    def _signing_key() -> bytes:
        secret = settings.JWT_SECRET_KEY
        if not secret or len(secret.encode("utf-8")) < 32:
            raise AuthConfigurationError(
                "JWT_SECRET_KEY must contain at least 32 bytes."
            )
        return secret.encode("utf-8")

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def register(
        self,
        email: str,
        password: str,
        name: str = "",
    ) -> tuple[Dict[str, Any], str]:
        """Create an unverified regular account and return its one-time token."""
        self._signing_key()
        normalized_email = normalize_email(email)
        password_bytes = validate_password(password)
        normalized_name = validate_name(name)
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=settings.EMAIL_VERIFICATION_MINUTES)
        password_hash = bcrypt.hashpw(
            password_bytes,
            bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS),
        ).decode("ascii")

        try:
            user = self.session_store.create_user(
                user_id=str(uuid.uuid4()),
                email=normalized_email,
                password_hash=password_hash,
                role="user",
                name=normalized_name,
                verification_token_hash=self._token_hash(token),
                verification_expires_at=expires_at.isoformat(),
                verification_sent_at=now.isoformat(),
            )
            return user, token
        except sqlite3.IntegrityError as error:
            raise EmailAlreadyRegisteredError(
                "Unable to register with these account details."
            ) from error

    def verify_email(self, token: str) -> bool:
        """Consume and verify one matching, unexpired token."""
        if not isinstance(token, str) or not 1 <= len(token) <= 256:
            return False
        return self.session_store.consume_email_verification(
            self._token_hash(token),
            datetime.now(timezone.utc).isoformat(),
        )

    def resend_verification(self, email: str) -> Optional[tuple[str, str]]:
        """Issue a replacement token only for an existing unverified account."""
        self._signing_key()
        normalized_email = normalize_email(email)
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=settings.EMAIL_VERIFICATION_MINUTES)
        matched_email = self.session_store.issue_email_verification(
            normalized_email,
            self._token_hash(token),
            expires_at.isoformat(),
            now.isoformat(),
        )
        if matched_email is None:
            return None
        return matched_email, token

    def request_password_reset(
        self,
        email: str,
        source: str = "unknown",
        client_ip: Optional[str] = None,
    ) -> Optional[tuple[str, str]]:
        """Issue a one-time reset token for an existing verified account."""
        normalized_email = normalize_email(email)
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=settings.PASSWORD_RESET_MINUTES)
        recipient = self.session_store.issue_password_reset(
            normalized_email,
            self._token_hash(token),
            expires_at.isoformat(),
            now.isoformat(),
        )
        self.record_audit_event(
            "password_reset_requested",
            {
                "source": source,
                "ip_masked": mask_ip_address(client_ip),
            },
        )
        if recipient is None:
            return None
        return recipient, token

    def reset_password(
        self,
        token: str,
        password: str,
        source: str = "unknown",
        client_ip: Optional[str] = None,
    ) -> bool:
        """Replace a password and atomically consume its valid reset token."""
        if not isinstance(token, str) or not 1 <= len(token) <= 256:
            return False
        password_bytes = validate_new_password(password)
        password_hash = bcrypt.hashpw(
            password_bytes,
            bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS),
        ).decode("ascii")
        completed = self.session_store.consume_password_reset(
            self._token_hash(token),
            password_hash,
            datetime.now(timezone.utc).isoformat(),
            source,
            mask_ip_address(client_ip),
        )
        return completed

    def login(
        self,
        email: str,
        password: str,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        source: str = "unknown",
    ) -> Dict[str, Any]:
        """Verify credentials and return an access token with safe user details."""
        self._signing_key()
        normalized_email = normalize_email(email)
        password_bytes = validate_password(password)
        stored_user = self.session_store.get_user_for_auth_by_email(normalized_email)

        if stored_user is None:
            if self._dummy_password_hash is None:
                self._dummy_password_hash = bcrypt.hashpw(
                    secrets.token_bytes(32),
                    bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS),
                )
            bcrypt.checkpw(password_bytes, self._dummy_password_hash)
            self._record_login_failure(
                normalized_email,
                source,
                client_ip,
                user_agent,
                "account_not_found",
            )
            raise InvalidCredentialsError("Invalid email or password.")

        try:
            password_matches = bcrypt.checkpw(
                password_bytes,
                stored_user["password_hash"].encode("ascii"),
            )
        except (ValueError, TypeError):
            password_matches = False
        if not password_matches:
            self._record_login_failure(
                normalized_email,
                source,
                client_ip,
                user_agent,
                "invalid_credentials",
            )
            raise InvalidCredentialsError("Invalid email or password.")
        if not stored_user["email_verified"]:
            self._record_login_failure(
                normalized_email,
                source,
                client_ip,
                user_agent,
                "security_action",
            )
            raise EmailNotVerifiedError("Email verification is required.")

        user = {
            "user_id": stored_user["user_id"],
            "name": stored_user["name"],
            "email": stored_user["email"],
            "role": stored_user["role"],
            "created_at": stored_user["created_at"],
            "email_verified": bool(stored_user["email_verified"]),
        }
        now = datetime.now(timezone.utc)
        session_id = secrets.token_urlsafe(32)
        expires_at = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES)
        access_token = self.create_access_token(
            user["user_id"],
            stored_user["auth_token_version"],
            session_id,
        )
        self.session_store.create_auth_session(
            session_id,
            user["user_id"],
            now.isoformat(),
            expires_at.isoformat(),
            client_ip,
            user_agent,
            source,
        )
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": settings.JWT_ACCESS_TOKEN_MINUTES * 60,
            "user": user,
        }

    def create_access_token(
        self,
        user_id: str,
        auth_token_version: int = 0,
        session_id: Optional[str] = None,
    ) -> str:
        """Create a signed access token without embedding mutable role claims."""
        if type(auth_token_version) is not int or auth_token_version < 0:
            raise ValueError("Invalid authentication token version.")
        if session_id is not None and not 32 <= len(session_id) <= 128:
            raise ValueError("Invalid authentication session identifier.")
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user_id,
            "iss": _TOKEN_ISSUER,
            "auth_token_version": auth_token_version,
            "iat": now,
            "exp": now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES),
        }
        if session_id is not None:
            payload["sid"] = session_id
        return jwt.encode(payload, self._signing_key(), algorithm="HS256")

    def resolve_access_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate a token and load current role and identity from SQLite."""
        try:
            payload = jwt.decode(
                token,
                self._signing_key(),
                algorithms=["HS256"],
                issuer=_TOKEN_ISSUER,
                options={"require": ["exp", "iat", "iss", "sub"]},
            )
        except jwt.InvalidTokenError:
            return None

        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id:
            return None
        token_version = payload.get("auth_token_version", 0)
        if type(token_version) is not int or token_version < 0:
            return None
        session_id = payload.get("sid")
        if session_id is not None and (
            not isinstance(session_id, str)
            or not 32 <= len(session_id) <= 128
        ):
            return None
        user = self.session_store.get_user_for_auth_by_id(user_id)
        if (
            user is None
            or not user["email_verified"]
            or token_version != user["auth_token_version"]
        ):
            return None
        if session_id is not None and not self.session_store.touch_auth_session(
            session_id,
            user_id,
            datetime.now(timezone.utc).isoformat(),
        ):
            return None
        resolved_user = {
            "user_id": user["user_id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
            "created_at": user["created_at"],
            "email_verified": bool(user["email_verified"]),
        }
        if session_id is not None:
            resolved_user["session_id"] = session_id
        return resolved_user

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        revoke_other_sessions: bool = False,
        current_session_id: Optional[str] = None,
        source: str = "unknown",
    ) -> int:
        """Replace a password and revoke every other active session.

        ``revoke_other_sessions`` remains accepted for compatibility; session
        revocation is now unconditional.
        """
        if type(revoke_other_sessions) is not bool:
            raise ValueError("Invalid session revocation option.")
        user = self._require_current_password(user_id, current_password)
        new_password_bytes = validate_new_password(new_password)
        if bcrypt.checkpw(
            new_password_bytes,
            user["password_hash"].encode("ascii"),
        ):
            raise PasswordReuseError(
                "New password must differ from the current password."
            )

        new_password_hash = bcrypt.hashpw(
            new_password_bytes,
            bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS),
        ).decode("ascii")
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES)
        version = self.session_store.update_password(
            user_id,
            user["password_hash"],
            new_password_hash,
            current_session_id,
            now.isoformat(),
            expires_at.isoformat(),
            source,
        )
        if version is None:
            raise InvalidCurrentPasswordError("Current password is incorrect.")
        return version

    def update_display_name(self, user_id: str, name: str) -> str:
        """Validate and update the account's display name."""
        normalized_name = validate_name(name)
        if not self.session_store.update_display_name(user_id, normalized_name):
            raise ValueError("Account is unavailable.")
        return normalized_name

    def request_email_change(
        self,
        user_id: str,
        current_password: str,
        new_email: str,
        source: str = "unknown",
    ) -> tuple[str, str]:
        """Issue a one-time email-change token after password confirmation."""
        user = self._require_current_password(user_id, current_password)
        if not user["email_verified"]:
            raise ValueError("Email verification is required.")
        normalized_email = normalize_email(new_email)
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=settings.EMAIL_VERIFICATION_MINUTES)
        issued = self.session_store.issue_email_change(
            user_id,
            normalized_email,
            self._token_hash(token),
            expires_at.isoformat(),
            now.isoformat(),
            source,
        )
        if not issued:
            raise EmailAlreadyInUseError(
                "That email address cannot be used."
            )
        return normalized_email, token

    def verify_email_change(
        self,
        token: str,
        source: str = "unknown",
    ) -> bool:
        """Consume an email-change token and update the address once."""
        if not isinstance(token, str) or not 1 <= len(token) <= 256:
            return False
        changed = self.session_store.consume_email_change(
            self._token_hash(token),
            datetime.now(timezone.utc).isoformat(),
            source,
        )
        return changed

    def delete_account(
        self,
        user_id: str,
        current_password: str,
        source: str = "unknown",
    ) -> None:
        """Delete an account only after confirming its current password."""
        user = self._require_current_password(user_id, current_password)
        self.record_audit_event(
            "account_deletion_requested",
            {"source": source},
            user_id,
        )
        if not self.session_store.delete_user(user_id, user["password_hash"]):
            raise InvalidCurrentPasswordError("Current password is incorrect.")

    def revoke_all_sessions(
        self,
        user_id: str,
        source: str = "unknown",
    ) -> bool:
        """Revoke every issued access token for one account."""
        return (
            self.session_store.revoke_all_user_sessions(user_id, source)
            is not None
        )

    def list_active_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """List only active sessions belonging to the requested account."""
        return self.session_store.list_active_auth_sessions(
            user_id,
            datetime.now(timezone.utc).isoformat(),
        )

    def list_security_activity(
        self,
        user_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Load a bounded, user-scoped history of security events."""
        return self.session_store.list_user_audit_events(user_id, limit)

    def revoke_session(self, user_id: str, session_id: str) -> bool:
        """Revoke one active session belonging to the requested account."""
        if not isinstance(session_id, str) or not 32 <= len(session_id) <= 128:
            return False
        return self.session_store.revoke_auth_session(
            user_id,
            session_id,
            datetime.now(timezone.utc).isoformat(),
        )

    def record_audit_event(
        self,
        event_type: str,
        metadata: Dict[str, Any],
        user_id: Optional[str] = None,
    ) -> None:
        """Persist only validated, event-specific metadata."""
        self.session_store.record_audit_event(event_type, metadata, user_id)

    def _record_login_failure(
        self,
        identifier: str,
        source: str,
        client_ip: Optional[str],
        user_agent: Optional[str],
        reason_code: str,
    ) -> None:
        metadata: Dict[str, Any] = {
            "source": source,
            "ip_masked": mask_ip_address(client_ip),
            "user_agent_family": normalize_user_agent_family(user_agent),
            "reason_code": reason_code,
        }
        secret = settings.AUDIT_IDENTIFIER_HMAC_SECRET.get_secret_value()
        if secret:
            metadata["identifier_hash"] = hash_login_identifier(identifier, secret)
        self.record_audit_event("login_failed", metadata)

    def _require_current_password(
        self,
        user_id: str,
        current_password: str,
    ) -> Dict[str, Any]:
        password_bytes = validate_password(current_password)
        user = self.session_store.get_user_for_auth_by_id(user_id)
        if user is None:
            raise InvalidCurrentPasswordError("Current password is incorrect.")
        try:
            password_matches = bcrypt.checkpw(
                password_bytes,
                user["password_hash"].encode("ascii"),
            )
        except (ValueError, TypeError):
            password_matches = False
        if not password_matches:
            raise InvalidCurrentPasswordError("Current password is incorrect.")
        return user
