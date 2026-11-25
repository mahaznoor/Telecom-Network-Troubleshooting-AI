"""Session state management utilities."""

import streamlit as st
import uuid


def init_app_session_state():
    """Initialize all application session state variables."""
    defaults = {
        "current_page": "overview",
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


def get_session_id() -> str:
    """Get or create session ID."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    return st.session_state.session_id


def new_session():
    """Create a new chat session."""
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []


def add_message(role: str, content: str, sources: list = None):
    """Add a message to the current session."""
    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    message = {
        "role": role,
        "content": content,
        "sources": sources or [],
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