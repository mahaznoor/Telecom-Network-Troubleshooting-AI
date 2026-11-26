"""System Health page - Professional monitoring dashboard."""

import streamlit as st
from datetime import datetime
from typing import Any, Dict, List
from frontend.components import (
    init_app_session_state,
    health_check,
    get_health_status,
    set_health_status,
)
from frontend.components.health_status import (
    get_groq_key_status,
    get_vector_db_status,
)


def format_timestamp(ts: str = None) -> str:
    """Format timestamp for display."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (AttributeError, TypeError, ValueError):
        return ts


def build_component_statuses(health: Dict[str, Any], last_check: str) -> List[Dict[str, str]]:
    """Build component indicators from health data returned by the backend."""
    vector_status = get_vector_db_status(health)
    if vector_status["status"] == "available" and vector_status["indexed_chunks"] == 0:
        vector_db_status = "empty"
    elif vector_status["status"] == "available":
        vector_db_status = "available"
    else:
        vector_db_status = vector_status["status"]

    groq_key_status = get_groq_key_status(health)
    groq_configured = health.get("groq_configured")
    groq_client_initialized = health.get("groq_client_initialized")
    if groq_key_status["status"] == "not_configured":
        groq_status = "not_configured"
        groq_details = "Credentials are not configured"
    elif groq_configured is True and groq_client_initialized is False:
        groq_status = "degraded"
        groq_details = "Credentials are configured, but client initialization failed"
    elif groq_configured is True and groq_client_initialized is True:
        groq_status = "configured"
        groq_details = "Client initialized; provider connectivity was not tested"
    elif groq_configured is True:
        groq_status = "configured"
        groq_details = "Credentials are configured; provider connectivity was not tested"
    else:
        groq_status = "unknown"
        groq_details = "Configuration status was not reported"

    embedding_model = health.get("embedding_model")
    if embedding_model and embedding_model != "unknown":
        embedding_status = "configured"
        embedding_details = f"{embedding_model}; inference was not tested"
    else:
        embedding_status = "unknown"
        embedding_details = "Model configuration was not reported"

    if vector_db_status == "available":
        indexed_chunks = vector_status["indexed_chunks"]
        vector_db_details = (
            f"{indexed_chunks} indexed chunks"
            if indexed_chunks is not None
            else "Indexed chunk count was not reported"
        )
    elif vector_db_status == "empty":
        vector_db_details = "Database is available but contains no indexed chunks"
    elif vector_db_status == "unavailable":
        vector_db_details = "Database query failed"
    else:
        vector_db_details = "Database availability was not reported"

    return [
        {
            "name": "FastAPI Backend",
            "icon": "🚀",
            "status": "online",
            "description": "Health endpoint returned a response",
            "details": f"API v{health.get('version', 'unknown')}",
            "last_check": last_check,
        },
        {
            "name": "Groq LLM",
            "icon": "🤖",
            "status": groq_status,
            "description": "Credential and local client state; remote connectivity is not probed",
            "details": f"{health.get('llm_model', 'Model not reported')} | {groq_details}",
            "last_check": last_check,
        },
        {
            "name": "HuggingFace Embeddings",
            "icon": "🧠",
            "status": embedding_status,
            "description": "Configured local embedding model; inference is not probed",
            "details": embedding_details,
            "last_check": last_check,
        },
        {
            "name": "ChromaDB Vector Store",
            "icon": "🗄️",
            "status": vector_db_status,
            "description": "Availability and indexed chunk count from the backend",
            "details": vector_db_details,
            "last_check": last_check,
        },
        {
            "name": "Document Ingestion Pipeline",
            "icon": "📥",
            "status": "not_checked",
            "description": "A health check does not run ingestion or write to the vector store",
            "details": "Verify ingestion by using the Knowledge Base upload workflow",
            "last_check": last_check,
        },
    ]


def render_component_card(component: dict):
    """Render a system component health card."""
    status = component.get("status", "unknown")
    status_colors = {
        "online": ("success", "🟢", "var(--color-success)"),
        "available": ("success", "🟢", "var(--color-success)"),
        "degraded": ("warning", "🟡", "var(--color-warning)"),
        "empty": ("warning", "🟡", "var(--color-warning)"),
        "offline": ("error", "🔴", "var(--color-error)"),
        "unavailable": ("error", "🔴", "var(--color-error)"),
        "not_configured": ("neutral", "⚪", "var(--color-text-muted)"),
        "configured": ("neutral", "⚙️", "var(--color-text-muted)"),
        "not_checked": ("neutral", "⚪", "var(--color-text-muted)"),
        "unknown": ("neutral", "⚪", "var(--color-text-muted)"),
    }
    status_class, status_icon, _ = status_colors.get(status, ("neutral", "⚪", "var(--color-text-muted)"))
    
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
        if st.button("🔄 Refresh Health", width="stretch", type="primary", key="sh_refresh"):
            with st.spinner("Checking system health..."):
                result = health_check()
                set_health_status(result)
                st.session_state["health_status_checked_at"] = datetime.now().isoformat()
            st.rerun()
    
    # Get health status
    health = get_health_status()
    last_check = format_timestamp(st.session_state.get("health_status_checked_at"))
    
    if not health:
        st.info("Click 'Refresh Health' to check system status.")
        return
    
    if "error" in health:
        st.error(health["error"], title="Health check failed")
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
            <div style="font-size: 0.75rem; color: var(--color-text-muted);">Groq provider connectivity and embedding inference are not tested.</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Component health cards
    st.markdown("### Component Status")
    
    components = build_component_statuses(health, last_check)
    
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
        vector_status = get_vector_db_status(health)
        indexed_chunks = vector_status["indexed_chunks"]
        st.metric(
            "Indexed Chunks",
            indexed_chunks if indexed_chunks is not None else "Unknown",
            help="Number of chunks reported by the vector store"
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
    
    groq_key_status = get_groq_key_status(health)
    groq_client_initialized = health.get("groq_client_initialized")
    groq_credentials = (groq_key_status["label"], "neutral")

    if groq_client_initialized is None:
        groq_client = ("Unreported", "neutral")
    elif groq_client_initialized:
        groq_client = ("Initialized", "success")
    else:
        groq_client = ("Not initialized", "warning")

    vector_status = get_vector_db_status(health)
    if vector_status["status"] == "unknown":
        vector_db = ("Unreported", "neutral")
    elif vector_status["status"] == "unavailable":
        vector_db = ("Unavailable", "error")
    elif vector_status["indexed_chunks"] != 0:
        vector_db = ("Available", "success")
    else:
        vector_db = ("Available, empty", "warning")

    embedding_model = health.get("embedding_model")
    configs = [
        ("Groq credentials", groq_credentials[0], groq_credentials[1]),
        ("Groq client initialization", groq_client[0], groq_client[1]),
        ("Embedding model", embedding_model or "Unreported", "neutral"),
        ("ChromaDB query", vector_db[0], vector_db[1]),
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