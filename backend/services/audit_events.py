"""Strict metadata validation and normalization for authentication audit events."""

import hashlib
import hmac
import ipaddress
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


EVENT_METADATA_ALLOWLIST = {
    "login_success": frozenset(
        {"source", "ip_masked", "user_agent_family"}
    ),
    "login_failed": frozenset(
        {
            "source",
            "ip_masked",
            "user_agent_family",
            "reason_code",
            "identifier_hash",
        }
    ),
    "session_created": frozenset(
        {
            "source",
            "ip_masked",
            "user_agent_family",
            "session_expires_at",
        }
    ),
    "session_revoked": frozenset({"reason_code", "revoked_by"}),
    "logout_all_sessions": frozenset({"revoked_session_count", "source"}),
    "password_changed": frozenset({"source"}),
    "password_reset_requested": frozenset({"source", "ip_masked"}),
    "password_reset_completed": frozenset({"source", "ip_masked"}),
    "email_change_requested": frozenset({"source"}),
    "email_changed": frozenset({"source"}),
    "account_deletion_requested": frozenset({"source"}),
    "account_deleted": frozenset({"deletion_scope"}),
}

_SOURCES = frozenset({"web", "api", "unknown"})
_REASON_CODES = frozenset(
    {
        "invalid_credentials",
        "account_not_found",
        "account_disabled",
        "expired_session",
        "user_initiated",
        "password_reset",
        "security_action",
        "unknown",
    }
)
_USER_AGENT_FAMILIES = frozenset(
    {"Chrome", "Firefox", "Edge", "Safari", "MobileApp", "Unknown"}
)
_REVOKED_BY = frozenset({"user", "admin", "system"})
_DELETION_SCOPES = frozenset(
    {"user_data_removed", "activity_history_removed", "completed"}
)
_OPTIONAL_FIELDS = frozenset(
    {"ip_masked", "user_agent_family", "reason_code"}
)
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class AuditMetadataValidationError(ValueError):
    """Raised when an audit event or its metadata violates the allowlist."""


def mask_ip_address(value: Any) -> Optional[str]:
    """Mask a client address to IPv4 /24 or IPv6 /48, or omit it safely."""
    if not isinstance(value, str):
        return None
    try:
        address = ipaddress.ip_address(value.strip())
        prefix = 24 if address.version == 4 else 48
        return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))
    except ValueError:
        return None


def normalize_user_agent_family(value: Any) -> Optional[str]:
    """Convert a raw user-agent to a small browser/client category."""
    if not isinstance(value, str) or not value.strip():
        return None

    agent = value.casefold()
    if "mobileapp" in agent or "telecomnetworktroubleshootingai/" in agent:
        return "MobileApp"
    if "edg/" in agent or "edge/" in agent:
        return "Edge"
    if "firefox/" in agent:
        return "Firefox"
    if "chrome/" in agent or "chromium/" in agent:
        return "Chrome"
    if (
        "safari/" in agent
        and "chrome/" not in agent
        and "chromium/" not in agent
    ):
        return "Safari"
    return "Unknown"


def hash_login_identifier(identifier: str, secret: str) -> str:
    """Return a keyed digest of a normalized login identifier."""
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise AuditMetadataValidationError(
            "The audit identifier HMAC secret must contain at least 32 bytes."
        )
    if not isinstance(identifier, str) or not identifier.strip():
        raise AuditMetadataValidationError("Invalid login identifier.")

    normalized = identifier.strip().casefold()
    return hmac.new(
        secret.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _validate_masked_ip(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise AuditMetadataValidationError("Invalid masked IP address.")
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise AuditMetadataValidationError(
            "Invalid masked IP address."
        ) from error
    expected_prefix = 24 if network.version == 4 else 48
    if network.prefixlen != expected_prefix:
        raise AuditMetadataValidationError("Invalid masked IP address.")
    return str(network)


def _validate_session_expiry(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise AuditMetadataValidationError("Invalid session expiry timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AuditMetadataValidationError(
            "Invalid session expiry timestamp."
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise AuditMetadataValidationError("Session expiry must be UTC.")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_field(key: str, value: Any) -> Any:
    if key == "source":
        if not isinstance(value, str) or value not in _SOURCES:
            raise AuditMetadataValidationError("Invalid audit source.")
        return value
    if key == "ip_masked":
        return _validate_masked_ip(value)
    if key == "user_agent_family":
        if (
            not isinstance(value, str)
            or len(value) > 50
            or value not in _USER_AGENT_FAMILIES
        ):
            raise AuditMetadataValidationError("Invalid user-agent family.")
        return value
    if key == "reason_code":
        if (
            not isinstance(value, str)
            or len(value) > 50
            or value not in _REASON_CODES
        ):
            raise AuditMetadataValidationError("Invalid audit reason code.")
        return value
    if key == "identifier_hash":
        if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
            raise AuditMetadataValidationError("Invalid identifier hash.")
        return value
    if key == "session_expires_at":
        return _validate_session_expiry(value)
    if key == "revoked_session_count":
        if type(value) is not int or value < 0:
            raise AuditMetadataValidationError("Invalid revoked session count.")
        return value
    if key == "revoked_by":
        if not isinstance(value, str) or value not in _REVOKED_BY:
            raise AuditMetadataValidationError("Invalid session revoker.")
        return value
    if key == "deletion_scope":
        if not isinstance(value, str) or value not in _DELETION_SCOPES:
            raise AuditMetadataValidationError("Invalid deletion scope.")
        return value
    raise AuditMetadataValidationError("Unsupported audit metadata field.")


def validate_event_metadata(
    event_type: str,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate metadata against exactly one event's schema."""
    if not isinstance(event_type, str):
        raise AuditMetadataValidationError("Unknown audit event type.")
    allowed_keys = EVENT_METADATA_ALLOWLIST.get(event_type)
    if allowed_keys is None:
        raise AuditMetadataValidationError("Unknown audit event type.")
    if not isinstance(metadata, Mapping):
        raise AuditMetadataValidationError("Audit metadata must be an object.")

    unknown_keys = set(metadata) - allowed_keys
    if unknown_keys:
        raise AuditMetadataValidationError("Unknown audit metadata field.")

    validated = {}
    for key, value in metadata.items():
        if value is None and key in _OPTIONAL_FIELDS:
            continue
        validated[key] = _validate_field(key, value)
    serialized = json.dumps(validated, separators=(",", ":"), ensure_ascii=True)
    if len(serialized.encode("utf-8")) > 1024:
        raise AuditMetadataValidationError("Audit metadata exceeds 1 KB.")
    return validated
