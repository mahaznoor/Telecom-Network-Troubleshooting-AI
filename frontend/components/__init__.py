"""Frontend components package."""

from frontend.components.sidebar import render_sidebar, get_current_page
from frontend.components.api_client import (
    call_api,
    health_check,
    ingest_documents,
    chat_question,
    clear_session,
    clear_all_sessions,
    get_collection_stats,
)
from frontend.components.session_state import (
    init_app_session_state,
    get_session_id,
    new_session,
    add_message,
    clear_session_history,
    get_chat_history,
    set_health_status,
    get_health_status,
    set_ingestion_status,
    get_ingestion_status,
)

__all__ = [
    # Sidebar
    "render_sidebar",
    "get_current_page",
    # API Client
    "call_api",
    "health_check",
    "ingest_documents",
    "chat_question",
    "clear_session",
    "clear_all_sessions",
    "get_collection_stats",
    # Session State
    "init_app_session_state",
    "get_session_id",
    "new_session",
    "add_message",
    "clear_session_history",
    "get_chat_history",
    "set_health_status",
    "get_health_status",
    "set_ingestion_status",
    "get_ingestion_status",
]