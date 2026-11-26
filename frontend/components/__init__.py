"""Frontend components package."""

import streamlit as st

from frontend.components.sidebar import (
    render_sidebar,
    get_current_page as _get_current_page,
)
from frontend.components.api_client import (
    call_api,
    register_user,
    verify_email,
    resend_verification,
    login_user,
    change_password,
    logout_all_devices,
    list_active_sessions,
    get_security_activity,
    revoke_active_session,
    update_profile,
    request_email_change,
    verify_email_change,
    delete_account,
    health_check,
    ingest_documents,
    upload_documents,
    chat_question,
    submit_response_feedback,
    get_feedback_summary,
    list_chat_sessions,
    get_chat_session,
    clear_session,
    clear_all_sessions,
    get_collection_stats,
    search_documents,
    get_documents_list,
    delete_indexed_document,
    delete_vector_database,
)
from frontend.components.session_state import (
    init_app_session_state,
    get_session_id,
    new_session,
    set_active_session,
    add_message,
    clear_session_history,
    get_chat_history,
    set_health_status,
    get_health_status,
    set_ingestion_status,
    get_ingestion_status,
    set_authenticated_session,
    clear_authenticated_session,
    is_current_user_admin,
)


def get_current_page() -> str:
    """Route email action links to their public pages, including signed-in users."""
    if st.query_params.get("verify_email_change"):
        return "authentication"
    if st.query_params.get("reset_password"):
        return "password_reset"
    if st.query_params.get("verify_email"):
        return "authentication"
    return _get_current_page()


__all__ = [
    # Sidebar
    "render_sidebar",
    "get_current_page",
    # API Client
    "call_api",
    "register_user",
    "verify_email",
    "resend_verification",
    "login_user",
    "change_password",
    "logout_all_devices",
    "list_active_sessions",
    "get_security_activity",
    "revoke_active_session",
    "update_profile",
    "request_email_change",
    "verify_email_change",
    "delete_account",
    "health_check",
    "ingest_documents",
    "upload_documents",
    "chat_question",
    "submit_response_feedback",
    "get_feedback_summary",
    "list_chat_sessions",
    "get_chat_session",
    "clear_session",
    "clear_all_sessions",
    "get_collection_stats",
    "search_documents",
    "get_documents_list",
    "delete_indexed_document",
    "delete_vector_database",
    # Session State
    "init_app_session_state",
    "get_session_id",
    "new_session",
    "set_active_session",
    "add_message",
    "clear_session_history",
    "get_chat_history",
    "set_health_status",
    "get_health_status",
    "set_ingestion_status",
    "get_ingestion_status",
    "set_authenticated_session",
    "clear_authenticated_session",
    "is_current_user_admin",
]