"""Sidebar component with navigation and system status."""

import streamlit as st
from typing import Dict, Any, List, Optional
from frontend.components.api_client import (
    call_api,
    get_chat_session,
    list_chat_sessions,
)
from frontend.components.health_status import (
    get_groq_key_status,
    get_vector_db_status,
)
from frontend.components.session_state import (
    get_session_id,
    is_current_user_admin,
    clear_authenticated_session,
    new_session,
    set_active_session,
)


# Navigation pages configuration
NAVIGATION_ITEMS = [
    {"key": "overview", "label": "Overview", "icon": ":material/dashboard:"},
    {"key": "ai_assistant", "label": "AI Troubleshooting Assistant", "icon": ":material/smart_toy:"},
    {"key": "knowledge_base", "label": "Knowledge Base", "icon": ":material/menu_book:"},
    {"key": "network_docs", "label": "Network Documentation", "icon": ":material/folder_open:"},
    {"key": "system_health", "label": "System Health", "icon": ":material/monitor_heart:"},
    {"key": "chat_history", "label": "Chat History", "icon": ":material/chat:"},
    {"key": "feedback_dashboard", "label": "Feedback review", "icon": ":material/rate_review:"},
    {"key": "settings", "label": "Settings", "icon": ":material/settings:"},
]


def init_session_state():
    """Initialize session state for navigation and sidebar."""
    if "current_page" not in st.session_state:
        st.session_state.current_page = "overview"
    if "health_status" not in st.session_state:
        st.session_state.health_status = None
    if "sidebar_collapsed" not in st.session_state:
        st.session_state.sidebar_collapsed = False


def render_sidebar_brand():
    """Render the sidebar brand header."""
    st.markdown("""
    <div class="sidebar-brand">
        <div class="sidebar-brand-logo">TI</div>
        <h2 class="sidebar-brand-title">Telecom Intelligence</h2>
        <p class="sidebar-brand-subtitle">Network Operations Platform</p>
    </div>
    """, unsafe_allow_html=True)


def render_navigation():
    """Render navigation menu."""
    st.markdown('<div class="sidebar-nav">', unsafe_allow_html=True)
    
    for item in NAVIGATION_ITEMS:
        if (
            item["key"] == "feedback_dashboard"
            and not is_current_user_admin()
        ):
            continue
        is_active = st.session_state.current_page == item["key"]
        active_class = "active" if is_active else ""
        
        if st.button(
            f"{item['icon']}  {item['label']}",
            key=f"nav_{item['key']}",
            use_container_width=True,
            type="primary" if is_active else "secondary"
        ):
            st.session_state.current_page = item["key"]
            st.rerun()
    
    st.markdown('</div>', unsafe_allow_html=True)


def render_chat_sessions():
    """Render recent saved conversations and a reliable new-chat action."""
    st.markdown("#### Recent chats")
    if st.button("＋ New Chat", key="sidebar_new_chat", use_container_width=True):
        new_session()
        st.session_state.current_page = "ai_assistant"
        st.rerun()

    result = list_chat_sessions()
    if result.get("error"):
        st.caption("Saved chats are unavailable.")
        return

    current_session_id = get_session_id()
    sessions = result.get("sessions", [])
    if not sessions:
        st.caption("No saved conversations yet.")
        return

    for session in sessions[:10]:
        session_id = session.get("session_id")
        if not session_id:
            continue
        title = session.get("title") or "Untitled conversation"
        button_title = title if len(title) <= 32 else f"{title[:29].rstrip()}..."
        if st.button(
            f":material/chat: {button_title}",
            key=f"sidebar_chat_{session_id}",
            use_container_width=True,
            type="primary" if session_id == current_session_id else "secondary",
        ):
            conversation = get_chat_session(session_id)
            if conversation.get("error"):
                st.error(conversation["error"])
            else:
                set_active_session(
                    conversation["session_id"],
                    conversation.get("messages", []),
                )
                st.session_state.current_page = "ai_assistant"
                st.rerun()


def render_system_status():
    """Render system health status in sidebar footer."""
    if st.session_state.health_status:
        health = st.session_state.health_status
        
        if not health.get("error"):
            status_map = {
                "healthy": "online",
                "degraded": "degraded",
            }
            status_class = status_map.get(health.get("status", "").lower(), "unknown")
            groq_status = get_groq_key_status(health)
            vector_status = get_vector_db_status(health)
            indexed_chunks = vector_status["indexed_chunks"]
            groq_class = "unknown"
            db_class = {
                "available": "online",
                "unavailable": "offline",
                "unknown": "unknown",
            }[vector_status["status"]]
            
            st.markdown(f"""
            <div class="sidebar-footer">
                <div class="sidebar-status-item">
                    <span class="status-indicator {status_class}"></span>
                    <span>System: {health.get('status', 'unknown').title()}</span>
                </div>
                <div class="sidebar-status-item">
                    <span class="status-indicator {groq_class}"></span>
                    <span>Groq API key: {groq_status['label']}</span>
                </div>
                <div class="sidebar-status-item">
                    <span class="status-indicator {db_class}"></span>
                    <span>Vector DB: {vector_status['label']}</span>
                </div>
                <div class="sidebar-status-item">
                    <span class="status-indicator unknown"></span>
                    <span>Indexed chunks: {indexed_chunks if indexed_chunks is not None else 'Unknown'}</span>
                </div>
                <div class="sidebar-status-item" style="margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--color-divider);">
                    <span style="color: var(--color-text-muted); font-size: 10px;">v1.0.0 | Powered by Groq & LangChain</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="sidebar-footer">
                <div class="sidebar-status-item">
                    <span class="status-indicator offline"></span>
                    <span>Health check failed</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class="sidebar-footer">
            <div class="sidebar-status-item">
                <span class="status-indicator unknown"></span>
                <span>Click refresh to check status</span>
            </div>
        </div>
        """, unsafe_allow_html=True)


def render_sidebar():
    """Main sidebar render function."""
    init_session_state()
    if not st.session_state.get("access_token"):
        return
    
    with st.sidebar:
        render_sidebar_brand()
        current_user = st.session_state.get("current_user") or {}
        st.caption(
            f"{current_user.get('email', 'Signed in')} · "
            f"{current_user.get('role', 'user').title()}"
        )
        if st.button(
            "Sign out",
            key="sidebar_sign_out",
            width="stretch",
        ):
            clear_authenticated_session()
            st.rerun()
        render_navigation()
        render_chat_sessions()
        
        st.markdown("<div style='flex: 1;'></div>", unsafe_allow_html=True)
        
        # Refresh health button
        col1, col2 = st.columns([3, 1])
        with col1:
            if st.button(":material/refresh: Refresh", use_container_width=True, key="sidebar_refresh"):
                st.session_state.health_status = call_api("/health")
                st.rerun()
        with col2:
            st.caption("Health")
        
        render_system_status()


def get_current_page() -> str:
    """Get the currently active page."""
    if not st.session_state.get("access_token"):
        return "authentication"
    return st.session_state.get("current_page", "overview")