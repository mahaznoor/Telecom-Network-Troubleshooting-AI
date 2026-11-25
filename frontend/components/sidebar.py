"""Sidebar component with navigation and system status."""

import streamlit as st
from typing import Dict, Any, List, Optional
from frontend.components.api_client import call_api


# Navigation pages configuration
NAVIGATION_ITEMS = [
    {"key": "overview", "label": "Overview", "icon": "📊"},
    {"key": "ai_assistant", "label": "AI Troubleshooting Assistant", "icon": "🤖"},
    {"key": "knowledge_base", "label": "Knowledge Base", "icon": "📚"},
    {"key": "network_docs", "label": "Network Documentation", "icon": "🗂️"},
    {"key": "system_health", "label": "System Health", "icon": "🏥"},
    {"key": "chat_history", "label": "Chat History", "icon": "💬"},
    {"key": "settings", "label": "Settings", "icon": "⚙️"},
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


def render_system_status():
    """Render system health status in sidebar footer."""
    if st.session_state.health_status:
        health = st.session_state.health_status
        
        if "error" not in health:
            status_map = {
                "healthy": "online",
                "degraded": "degraded",
            }
            status_class = status_map.get(health.get("status", "").lower(), "unknown")
            
            groq_class = "online" if health.get("groq_configured") else "offline"
            db_class = "online" if health.get("vector_db_ready") else "offline"
            
            st.markdown(f"""
            <div class="sidebar-footer">
                <div class="sidebar-status-item">
                    <span class="status-indicator {status_class}"></span>
                    <span>System: {health.get('status', 'unknown').title()}</span>
                </div>
                <div class="sidebar-status-item">
                    <span class="status-indicator {groq_class}"></span>
                    <span>Groq API: {'Connected' if health.get('groq_configured') else 'Not Configured'}</span>
                </div>
                <div class="sidebar-status-item">
                    <span class="status-indicator {db_class}"></span>
                    <span>Vector DB: {'Ready' if health.get('vector_db_ready') else 'Empty'} ({health.get('document_count', 0)} docs)</span>
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
    
    with st.sidebar:
        render_sidebar_brand()
        render_navigation()
        
        st.markdown("<div style='flex: 1;'></div>", unsafe_allow_html=True)
        
        # Refresh health button
        col1, col2 = st.columns([3, 1])
        with col1:
            if st.button("🔄 Refresh", use_container_width=True, key="sidebar_refresh"):
                st.session_state.health_status = call_api("/health")
                st.rerun()
        with col2:
            st.caption("Health")
        
        render_system_status()


def get_current_page() -> str:
    """Get the currently active page."""
    return st.session_state.get("current_page", "overview")