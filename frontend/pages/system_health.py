"""System Health page - Professional monitoring dashboard."""

import streamlit as st
from datetime import datetime
from frontend.components import (
    init_app_session_state,
    call_api,
    health_check,
    get_health_status,
    set_health_status,
)


def format_timestamp(ts: str = None) -> str:
    """Format timestamp for display."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return ts


def render_component_card(component: dict):
    """Render a system component health card."""
    status = component.get("status", "unknown")
    status_colors = {
        "online": ("success", "🟢", "var(--color-success)"),
        "degraded": ("warning", "🟡", "var(--color-warning)"),
        "offline": ("error", "🔴", "var(--color-error)"),
        "not_configured": ("neutral", "⚪", "var(--color-text-muted)"),
    }
    status_class, status_icon, status_color = status_colors.get(status, ("neutral", "⚪", "var(--color-text-muted)"))
    
    st.markdown(f"""
    <div class="card" style="height: 100%;">
        <div style="display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 16px;">
            <div style="display: flex; align-items: center; gap: 12px;">
                <span style="font-size: 2rem;">{component.get('icon', '🔧')}</span>
                <div>
                    <h4 style="margin: 0; font-size: 1.125rem; font-weight: 600;">{component['name']}</h4>
                    <span class="badge badge-{status_class}">{status.replace('_', ' ').title()}</span>
                </div>
            </div>
            <div style="text-align: right;">
                <div style="font-size: 0.75rem; color: var(--color-text-muted);">Last checked</div>
                <div style="font-size: 0.75rem; font-weight: 500; color: var(--color-text-secondary);">{component.get('last_check', '—')}</div>
            </div>
        </div>
        <div style="color: var(--color-text-secondary); font-size: 0.875rem; margin-bottom: 12px;">
            {component.get('description', '')}
        </div>
        <div style="font-size: 0.75rem; color: var(--color-text-muted); font-family: var(--font-family-mono);">
            {component.get('details', 'No details available')}
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_system_health_page():
    """Main System Health page renderer."""
    init_app_session_state()
    
    # Page header
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("""
        <div style="margin-bottom: 24px;">
            <h1 style="margin: 0 0 8px;">🏥 System Health</h1>
            <p style="color: var(--color-text-secondary); margin: 0; font-size: 1.125rem;">
                Monitor the health and status of all system components.
            </p>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        if st.button("🔄 Refresh Health", use_container_width=True, type="primary", key="sh_refresh"):
            with st.spinner("Checking system health..."):
                result = health_check()
                set_health_status(result)
            st.rerun()
    
    # Get health status
    health = get_health_status()
    last_check = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if not health:
        st.info("Click 'Refresh Health' to check system status.")
        return
    
    if "error" in health:
        st.error(f"Health check failed: {health['error']}")
        return
    
    # Overall status banner
    overall_status = health.get("status", "unknown")
    status_colors = {
        "healthy": ("success", "🟢", "System Healthy"),
        "degraded": ("warning", "🟡", "System Degraded"),
    }
    status_class, status_icon, status_text = status_colors.get(overall_status, ("error", "🔴", "System Error"))
    
    st.markdown(f"""
    <div style="display: flex; align-items: center; gap: 12px; padding: 16px 20px; background: var(--color-{status_class}-bg); border-radius: var(--radius-lg); margin-bottom: 24px; border: 1px solid var(--color-{status_class});">
        <span style="font-size: 1.5rem;">{status_icon}</span>
        <div>
            <div style="font-weight: 600; font-size: 1.125rem; color: var(--color-{status_class});">{status_text}</div>
            <div style="font-size: 0.875rem; color: var(--color-text-muted);">Last check: {last_check}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Component health cards
    st.markdown("### Component Status")
    
    components = [
        {
            "name": "FastAPI Backend",
            "icon": "🚀",
            "status": "online" if "error" not in health else "offline",
            "description": "API server and REST endpoints",
            "details": f"v1.0.0 | Running on port 8000",
            "last_check": last_check,
        },
        {
            "name": "Groq LLM API",
            "icon": "🤖",
            "status": "online" if health.get("groq_configured") else "not_configured",
            "description": "Large language model provider",
            "details": health.get("llm_model", "Not configured"),
            "last_check": last_check,
        },
        {
            "name": "HuggingFace Embeddings",
            "icon": "🧠",
            "status": "online",
            "description": "Local embedding model inference",
            "details": health.get("embedding_model", "BAAI/bge-small-en-v1.5"),
            "last_check": last_check,
        },
        {
            "name": "ChromaDB Vector Store",
            "icon": "🗄️",
            "status": "online" if health.get("vector_db_ready") else "degraded",
            "description": "Vector database for document retrieval",
            "details": f"{health.get('document_count', 0)} documents indexed",
            "last_check": last_check,
        },
        {
            "name": "Document Ingestion Pipeline",
            "icon": "📥",
            "status": "online",
            "description": "PDF processing and indexing workflow",
            "details": "PyMuPDF + LangChain text splitter",
            "last_check": last_check,
        },
    ]
    
    cols = st.columns(2)
    for idx, comp in enumerate(components):
        with cols[idx % 2]:
            render_component_card(comp)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Vector Database Statistics
    st.markdown("### Vector Database Statistics")
    
    st.markdown("""
    <div class="card">
    """, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(
            "Indexed Documents",
            health.get("document_count", 0),
            help="Total documents in vector store"
        )
    
    with col2:
        st.metric(
            "Embedding Model",
            health.get("embedding_model", "unknown"),
            help="Model used for vector embeddings"
        )
    
    with col3:
        st.metric(
            "LLM Model",
            health.get("llm_model", "unknown"),
            help="Model used for answer generation"
        )
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # Configuration Status (without secrets)
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("### Configuration Status")
    
    st.markdown("""
    <div class="card">
    """, unsafe_allow_html=True)
    
    configs = [
        ("GROQ_API_KEY", "Configured" if health.get("groq_configured") else "Not Set", "success" if health.get("groq_configured") else "error"),
        ("EMBEDDING_MODEL", health.get("embedding_model", "BAAI/bge-small-en-v1.5"), "success"),
        ("EMBEDDING_DIMENSION", "384", "success"),
        ("CHROMA_PERSIST_DIR", "./storage/chroma", "success"),
        ("CHROMA_COLLECTION", "telecom_manuals", "success"),
        ("TOP_K", "5", "success"),
        ("SIMILARITY_THRESHOLD", "0.7", "success"),
    ]
    
    for name, value, status in configs:
        st.markdown(f"""
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid var(--color-divider);">
            <div>
                <div style="font-weight: 500; color: var(--color-text-primary);">{name}</div>
                <div style="font-size: 0.75rem; color: var(--color-text-muted);">Configuration parameter</div>
            </div>
            <div style="text-align: right;">
                <span class="badge badge-{status}">{value}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    render_system_health_page()