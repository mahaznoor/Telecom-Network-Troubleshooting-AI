"""Helpers for presenting verified backend health fields."""

from typing import Any, Dict, Optional


def get_groq_key_status(health: Dict[str, Any]) -> Dict[str, str]:
    """Describe key configuration without implying provider connectivity."""
    configured = health.get("groq_configured")
    if configured is True:
        return {
            "status": "configured",
            "label": "API key configured",
            "details": "Provider connectivity is not tested",
        }
    if configured is False:
        return {
            "status": "not_configured",
            "label": "Not configured",
            "details": "Provider connectivity is not tested",
        }
    return {
        "status": "unknown",
        "label": "Status not reported",
        "details": "Provider connectivity is not tested",
    }


def get_vector_db_status(health: Dict[str, Any]) -> Dict[str, Any]:
    """Separate verified database availability from its indexed chunk count."""
    available = health.get("vector_db_available")
    if available is True:
        count: Optional[int] = health.get("document_count")
        return {
            "status": "available",
            "label": "Available",
            "indexed_chunks": count,
        }
    if available is False:
        return {
            "status": "unavailable",
            "label": "Unavailable",
            "indexed_chunks": None,
        }
    return {
        "status": "unknown",
        "label": "Availability not reported",
        "indexed_chunks": None,
    }
