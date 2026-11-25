"""Chat History page - Session management interface."""

import streamlit as st
from datetime import datetime
from frontend.components import (
    init_app_session_state,
    get_chat_history,
    get_session_id,
    new_session,
    clear_session_history,
    call_api,
    clear_session,
)


def format_timestamp(ts: str = None) -> str:
    """Format timestamp for display."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M")
    except:
        return ts


def render_chat_sessions():
    """Render list of chat sessions."""
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Chat Sessions</h3>
            <p class="card-subtitle">Manage and resume previous conversations</p>
        </div>
    """, unsafe_allow_html=True)
    
    # Current session info
    current_session = get_session_id()
    messages = get_chat_history()
    
    st.markdown(f"""
    <div style="display: flex; align-items: center; justify-content: space-between; padding: 16px; background: var(--color-primary-light); border-radius: var(--radius-md); margin-bottom: 16px; border: 1px solid var(--color-primary);">
        <div style="display: flex; align-items: center; gap: 12px;">
            <span style="font-size: 1.5rem;">💬</span>
            <div>
                <div style="font-weight: 600; color: var(--color-primary);">Current Session</div>
                <div style="font-size: 0.875rem; color: var(--color-text-muted); font-family: var(--font-family-mono);">{current_session[:8]}...</div>
            </div>
        </div>
        <div style="display: flex; gap: 8px;">
            <span class="badge badge-primary">{len(messages)} messages</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Session actions
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 New Session", use_container_width=True, key="ch_new_session"):
            new_session()
            st.rerun()
    with col2:
        if st.button("🗑️ Clear Current History", use_container_width=True, key="ch_clear_current"):
            clear_session_history()
            st.rerun()
    with col3:
        if st.button("🗑️ Clear All Sessions", use_container_width=True, key="ch_clear_all"):
            result = call_api("/chat", "DELETE")
            if "error" not in result:
                clear_session_history()
                st.success("All sessions cleared")
                st.rerun()
            else:
                st.error(f"Failed: {result['error']}")
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # Show current session messages
    if messages:
        st.markdown("### Current Session Messages")
        
        for idx, msg in enumerate(messages):
            role = msg["role"]
            content = msg["content"]
            sources = msg.get("sources", [])
            
            with st.expander(f"{'👤' if role == 'user' else '🤖'} {role.title()}: {content[:80]}{'...' if len(content) > 80 else ''}", expanded=False):
                st.markdown(f"**Role:** {role.title()}")
                st.markdown(f"**Content:**")
                st.markdown(content)
                if sources:
                    st.markdown(f"**Sources ({len(sources)}):**")
                    for i, source in enumerate(sources, 1):
                        st.caption(f"{i}. {source.get('source_file', 'Unknown')} - Page {source.get('page_number', 'Unknown')}")
    else:
        st.markdown("""
        <div class="empty-state" style="padding: 32px 16px;">
            <div class="empty-state-icon">💬</div>
            <h4 class="empty-state-title">No Messages Yet</h4>
            <p class="empty-state-description">Start a conversation in the AI Assistant to see messages here.</p>
        </div>
        """, unsafe_allow_html=True)


def render_chat_history_page():
    """Main Chat History page renderer."""
    init_app_session_state()
    
    # Page header
    st.markdown("""
    <div style="margin-bottom: 24px;">
        <h1 style="margin: 0 0 8px;">💬 Chat History</h1>
        <p style="color: var(--color-text-secondary); margin: 0; font-size: 1.125rem;">
            View and manage your troubleshooting conversation sessions.
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    # Quick actions
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        if st.button("🤖 Open AI Assistant", use_container_width=True, type="primary", key="ch_open_assistant"):
            st.session_state.current_page = "ai_assistant"
            st.rerun()
    with col2:
        if st.button("🔄 New Session", use_container_width=True, key="ch_header_new"):
            new_session()
            st.rerun()
    with col3:
        if st.button("🗑️ Clear All", use_container_width=True, key="ch_header_clear"):
            result = call_api("/chat", "DELETE")
            if "error" not in result:
                clear_session_history()
                st.success("All sessions cleared")
                st.rerun()
            else:
                st.error(f"Failed: {result['error']}")
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Render sessions
    render_chat_sessions()


if __name__ == "__main__":
    render_chat_history_page()