"""Session state management utilities."""

import uuid
from typing import Optional

import streamlit as st


def init_app_session_state():
    """Initialize all application session state variables."""
    defaults = {
        "current_page": "overview",
        "access_token": None,
        "current_user": None,
        "auth_notice": None,
        "auth_error": None,
        "auth_success": None,
        "auth_success_title": None,
        "session_id": str(uuid.uuid4()),
        "messages": [],
        "health_status": None,
        "ingestion_status": None,
        "documents_list": None,
        "chat_sessions": {},
        "top_k": 5,
        "use_mmr": True,
        "sidebar_collapsed": False,
        "last_query_time": None,
    }
    
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def set_authenticated_session(auth_response: dict) -> None:
    """Keep the bearer token in server-side Streamlit session state."""
    for key in (
        "settings_active_sessions",
        "settings_sessions_owner",
        "settings_sessions_error",
        "settings_sessions_success",
    ):
        st.session_state.pop(key, None)
    st.session_state.access_token = auth_response["access_token"]
    st.session_state.current_user = auth_response["user"]
    st.session_state.auth_notice = None
    st.session_state.auth_error = None
    st.session_state.auth_success = None
    st.session_state.auth_success_title = None


def clear_authenticated_session(notice: Optional[str] = None) -> None:
    """Clear credentials and private chat state on logout or token expiry."""
    for key in (
        "settings_active_sessions",
        "settings_sessions_owner",
        "settings_sessions_error",
        "settings_sessions_success",
    ):
        st.session_state.pop(key, None)
    st.session_state.access_token = None
    st.session_state.current_user = None
    st.session_state.messages = []
    st.session_state.chat_sessions = {}
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.current_page = "overview"
    st.session_state.auth_notice = notice
    st.session_state.auth_error = None
    st.session_state.auth_success = None
    st.session_state.auth_success_title = None


def is_current_user_admin() -> bool:
    """Return whether the current frontend identity has the admin role."""
    current_user = st.session_state.get("current_user") or {}
    return current_user.get("role") == "admin"


def get_session_id() -> str:
    """Get or create session ID."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    return st.session_state.session_id


def new_session():
    """Create a new chat session."""
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []


def set_active_session(session_id: str, messages: list):
    """Switch the active conversation and restore its messages."""
    st.session_state.session_id = session_id
    st.session_state.messages = [
        {
            "role": message.get("role", "assistant"),
            "content": message.get("content", ""),
            "sources": message.get("sources", []),
            "message_id": message.get("message_id"),
        }
        for message in messages
    ]


def add_message(
    role: str,
    content: str,
    sources: list = None,
    message_id: Optional[int] = None,
):
    """Add a message to the current session."""
    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    message = {
        "role": role,
        "content": content,
        "sources": sources or [],
        "message_id": message_id,
    }
    st.session_state.messages.append(message)


def clear_session_history():
    """Clear the current session's message history."""
    st.session_state.messages = []


def get_chat_history() -> list:
    """Get the current session's message history."""
    return st.session_state.get("messages", [])


def set_health_status(status: dict):
    """Set health status in session state."""
    st.session_state.health_status = status


def get_health_status() -> dict:
    """Get health status from session state."""
    return st.session_state.get("health_status")


def set_ingestion_status(status: dict):
    """Set ingestion status in session state."""
    st.session_state.ingestion_status = status


def get_ingestion_status() -> dict:
    """Get ingestion status from session state."""
    return st.session_state.get("ingestion_status")