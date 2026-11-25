"""Overview dashboard page with real backend statistics."""

import streamlit as st
from datetime import datetime
from frontend.components import (
    init_app_session_state,
    health_check,
    get_health_status,
    get_ingestion_status,
    get_session_id,
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


def render_kpi_cards(health: dict):
    """Render KPI metric cards."""
    doc_count = health.get("document_count", 0)
    vector_ready = health.get("vector_db_ready", False)
    groq_ready = health.get("groq_configured", False)
    
    # We need to calculate chunks - let's get it from ingestion status or estimate
    ingestion = get_ingestion_status()
    chunks_count = ingestion.get("chunks_created", 0) if ingestion and ingestion.get("status") == "success" else 0
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-header">
                <div class="kpi-icon">📄</div>
                <span class="kpi-trend neutral">Total</span>
            </div>
            <div class="kpi-value">{doc_count}</div>
            <div class="kpi-label">Indexed Documents</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-header">
                <div class="kpi-icon">🧩</div>
                <span class="kpi-trend neutral">Total</span>
            </div>
            <div class="kpi-value">{chunks_count if chunks_count > 0 else '—'}</div>
            <div class="kpi-label">Knowledge Chunks</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        status_text = "Ready" if groq_ready else "Not Configured"
        status_class = "success" if groq_ready else "error"
        trend_class = "success" if groq_ready else "error"
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-header">
                <div class="kpi-icon {'success' if groq_ready else 'error'}">🤖</div>
                <span class="kpi-trend {trend_class}">{status_text}</span>
            </div>
            <div class="kpi-value" style="font-size: 1.25rem;">{status_text}</div>
            <div class="kpi-label">AI Service Status</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        db_text = "Healthy" if vector_ready else "Empty"
        db_class = "success" if vector_ready else "warning"
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-header">
                <div class="kpi-icon {'success' if vector_ready else 'warning'}">🗄️</div>
                <span class="kpi-trend {db_class}">{db_text}</span>
            </div>
            <div class="kpi-value" style="font-size: 1.25rem;">{db_text}</div>
            <div class="kpi-label">Vector Database</div>
        </div>
        """, unsafe_allow_html=True)


def render_knowledge_base_overview():
    """Render knowledge base overview section."""
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Knowledge Base Overview</h3>
        </div>
    """, unsafe_allow_html=True)
    
    health = get_health_status()
    ingestion = get_ingestion_status()
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(
            label="Uploaded PDFs",
            value=health.get("document_count", 0) if health else 0,
            help="Number of PDF documents ingested"
        )
    
    with col2:
        chunks = ingestion.get("chunks_created", 0) if ingestion and ingestion.get("status") == "success" else 0
        st.metric(
            label="Total Chunks",
            value=chunks,
            help="Total text chunks created from documents"
        )
    
    with col3:
        last_ingestion = ingestion.get("timestamp") if ingestion else None
        st.metric(
            label="Last Ingestion",
            value=format_timestamp(last_ingestion) if last_ingestion else "Never",
            help="Timestamp of last successful ingestion"
        )
    
    st.markdown("</div>", unsafe_allow_html=True)


def render_system_components():
    """Render system components status."""
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">System Components</h3>
        </div>
    """, unsafe_allow_html=True)
    
    health = get_health_status()
    
    components = [
        {
            "name": "FastAPI Backend",
            "status": "online" if health and "error" not in health else "offline",
            "description": "API server status",
            "details": f"v1.0.0 | {health.get('llm_model', 'unknown') if health else 'unknown'}"
        },
        {
            "name": "Groq LLM",
            "status": "online" if health and health.get("groq_configured") else "offline",
            "description": "Language model provider",
            "details": health.get("llm_model", "Not configured") if health else "Unknown"
        },
        {
            "name": "HuggingFace Embeddings",
            "status": "online",
            "description": "Embedding model service",
            "details": health.get("embedding_model", "BAAI/bge-small-en-v1.5") if health else "BAAI/bge-small-en-v1.5"
        },
        {
            "name": "ChromaDB Vector Store",
            "status": "online" if health and health.get("vector_db_ready") else "degraded",
            "description": "Vector database",
            "details": f"{health.get('document_count', 0)} documents indexed" if health else "Unknown"
        },
    ]
    
    for comp in components:
        status_colors = {
            "online": ("success", "🟢"),
            "degraded": ("warning", "🟡"),
            "offline": ("error", "🔴"),
        }
        status_class, status_icon = status_colors.get(comp["status"], ("neutral", "⚪"))
        
        st.markdown(f"""
        <div style="display: flex; align-items: center; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid var(--color-divider);">
            <div style="display: flex; align-items: center; gap: 12px;">
                <span style="font-size: 1.25rem;">{status_icon}</span>
                <div>
                    <div style="font-weight: 500; color: var(--color-text-primary);">{comp['name']}</div>
                    <div style="font-size: 0.875rem; color: var(--color-text-muted);">{comp['description']}</div>
                </div>
            </div>
            <div style="text-align: right;">
                <span class="badge badge-{status_class}">{comp['status'].title()}</span>
                <div style="font-size: 0.75rem; color: var(--color-text-muted); margin-top: 4px;">{comp['details']}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("</div>", unsafe_allow_html=True)


def render_recent_activity():
    """Render recent activity section."""
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Recent Activity</h3>
        </div>
    """, unsafe_allow_html=True)
    
    # Get recent messages from chat history
    from frontend.components.session_state import get_chat_history
    messages = get_chat_history()
    
    if messages:
        recent = messages[-5:]
        for msg in reversed(recent):
            role_icon = "👤" if msg["role"] == "user" else "🤖"
            role_label = "You" if msg["role"] == "user" else "AI Assistant"
            preview = msg["content"][:100] + ("..." if len(msg["content"]) > 100 else "")
            
            st.markdown(f"""
            <div style="display: flex; align-items: flex-start; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--color-divider);">
                <span style="font-size: 1.25rem; margin-top: 2px;">{role_icon}</span>
                <div style="flex: 1;">
                    <div style="font-weight: 500; color: var(--color-text-primary);">{role_label}</div>
                    <div style="font-size: 0.875rem; color: var(--color-text-secondary);">{preview}</div>
                </div>
                <div style="font-size: 0.75rem; color: var(--color-text-muted);">Just now</div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class="empty-state">
            <div class="empty-state-icon">💬</div>
            <h4 class="empty-state-title">No Recent Activity</h4>
            <p class="empty-state-description">Start a conversation with the AI Assistant to see activity here.</p>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("</div>", unsafe_allow_html=True)


def render_quick_actions():
    """Render quick action buttons."""
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Quick Actions</h3>
        </div>
    """, unsafe_allow_html=True)
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        if st.button("🤖 Open AI Assistant", use_container_width=True, key="qa_open_assistant"):
            st.session_state.current_page = "ai_assistant"
            st.rerun()
    
    with col2:
        if st.button("📤 Upload New Manual", use_container_width=True, key="qa_upload_manual"):
            st.session_state.current_page = "knowledge_base"
            st.rerun()
    
    with col3:
        if st.button("🗂️ View Documentation", use_container_width=True, key="qa_view_docs"):
            st.session_state.current_page = "network_docs"
            st.rerun()
    
    with col4:
        if st.button("🏥 Check System Health", use_container_width=True, key="qa_check_health"):
            st.session_state.current_page = "system_health"
            st.rerun()
    
    st.markdown("</div>", unsafe_allow_html=True)


def render_overview_page():
    """Main overview page renderer."""
    init_app_session_state()
    
    # Page header
    st.markdown("""
    <div style="margin-bottom: 24px;">
        <h1 style="margin: 0 0 8px;">Network Operations Overview</h1>
        <p style="color: var(--color-text-secondary); margin: 0; font-size: 1.125rem;">
            Monitor your telecom knowledge base and troubleshoot network issues with AI.
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    # System status indicator
    health = get_health_status()
    if health and "error" not in health:
        status = health.get("status", "unknown")
        status_class = "success" if status == "healthy" else "warning"
        st.markdown(f"""
        <div style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; background: var(--color-{status_class}-bg); border-radius: var(--radius-full); font-weight: 500; font-size: 0.875rem; color: var(--color-{status_class}); margin-bottom: 24px;">
            <span class="status-indicator {status_class}"></span>
            System Status: {status.title()}
        </div>
        """, unsafe_allow_html=True)
    
    # KPI Cards
    if health:
        render_kpi_cards(health)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Dashboard sections
    col_left, col_right = st.columns([2, 1])
    
    with col_left:
        render_knowledge_base_overview()
        st.markdown("<br>", unsafe_allow_html=True)
        render_recent_activity()
    
    with col_right:
        render_system_components()
        st.markdown("<br>", unsafe_allow_html=True)
        render_quick_actions()


if __name__ == "__main__":
    render_overview_page()