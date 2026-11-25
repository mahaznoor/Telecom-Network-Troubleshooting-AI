"""Settings page - Configuration display and management."""

import streamlit as st
from backend.config import settings
from frontend.components import (
    init_app_session_state,
    get_health_status,
    call_api,
)


def render_settings_page():
    """Main Settings page renderer."""
    init_app_session_state()
    
    # Page header
    st.markdown("""
    <div style="margin-bottom: 24px;">
        <h1 style="margin: 0 0 8px;">⚙️ Settings</h1>
        <p style="color: var(--color-text-secondary); margin: 0; font-size: 1.125rem;">
            Configure application behavior and view system configuration.
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    # AI Model Configuration
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">AI Model Configuration</h3>
            <p class="card-subtitle">Language model and retrieval settings</p>
        </div>
    """, unsafe_allow_html=True)
    
    health = get_health_status()
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**LLM Provider**")
        st.markdown(f"""
        <div style="padding: 12px; background: var(--color-bg-secondary); border-radius: var(--radius-md); margin-bottom: 16px;">
            <div style="font-weight: 500; color: var(--color-text-primary);">Groq</div>
            <div style="font-size: 0.875rem; color: var(--color-text-secondary);">{health.get('llm_model', 'llama-3.1-70b-versatile') if health else 'llama-3.1-70b-versatile'}</div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown("**Model Parameters**")
        st.markdown(f"""
        <div style="padding: 12px; background: var(--color-bg-secondary); border-radius: var(--radius-md); margin-bottom: 16px;">
            <div style="font-weight: 500; color: var(--color-text-primary);">Temperature</div>
            <div style="font-size: 0.875rem; color: var(--color-text-secondary);">{settings.LLM_TEMPERATURE}</div>
            <div style="font-size: 0.75rem; color: var(--color-text-muted);">Controls randomness in responses (set in .env)</div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown(f"""
        <div style="padding: 12px; background: var(--color-bg-secondary); border-radius: var(--radius-md); margin-bottom: 16px;">
            <div style="font-weight: 500; color: var(--color-text-primary);">Max Tokens</div>
            <div style="font-size: 0.875rem; color: var(--color-text-secondary);">{settings.LLM_MAX_TOKENS}</div>
            <div style="font-size: 0.75rem; color: var(--color-text-muted);">Maximum response length (set in .env)</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown("**Embedding Model**")
        st.markdown(f"""
        <div style="padding: 12px; background: var(--color-bg-secondary); border-radius: var(--radius-md); margin-bottom: 16px;">
            <div style="font-weight: 500; color: var(--color-text-primary);">HuggingFace</div>
            <div style="font-size: 0.875rem; color: var(--color-text-secondary);">{health.get('embedding_model', 'BAAI/bge-small-en-v1.5') if health else 'BAAI/bge-small-en-v1.5'}</div>
            <div style="font-size: 0.75rem; color: var(--color-text-muted);">Dimension: 384 | Device: CPU</div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown("**Retrieval Settings**")
        top_k = st.slider(
            "Top-K Results",
            1, 20, settings.TOP_K,
            help="Number of document chunks to retrieve",
            key="top_k"
        )
        
        similarity_threshold = st.slider(
            "Similarity Threshold",
            0.0, 1.0, settings.SIMILARITY_THRESHOLD, 0.05,
            help="Minimum similarity score for retrieved documents",
            key="similarity_threshold"
        )
        
        use_mmr = st.checkbox(
            "Use MMR (Maximum Marginal Relevance)",
            value=True,
            help="Enable diverse result retrieval",
            key="use_mmr"
        )
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # API Connection
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">API Connection</h3>
            <p class="card-subtitle">Backend service configuration</p>
        </div>
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**FastAPI Backend**")
        st.code("http://localhost:8000/api/v1", language="text")
        st.caption("Base URL for all API requests")
    
    with col2:
        st.markdown("**Groq API**")
        groq_status = "🟢 Connected" if health and health.get("groq_configured") else "🔴 Not Configured"
        st.markdown(f"""
        <div style="padding: 12px; background: var(--color-bg-secondary); border-radius: var(--radius-md); margin-bottom: 16px;">
            <div style="font-weight: 500;">{groq_status}</div>
            <div style="font-size: 0.875rem; color: var(--color-text-muted);">API key configured via environment variable</div>
        </div>
        """, unsafe_allow_html=True)
        st.caption("GROQ_API_KEY set in .env file")
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # Application Information
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Application Information</h3>
            <p class="card-subtitle">Version and build details</p>
        </div>
    """, unsafe_allow_html=True)
    
    app_info = [
        ("Application", "Telecom Intelligence AI"),
        ("Version", "1.0.0"),
        ("Architecture", "RAG with Groq LLM + BGE Embeddings"),
        ("Frontend", "Streamlit"),
        ("Backend", "FastAPI"),
        ("Vector Store", "ChromaDB"),
        ("Embedding Model", "BAAI/bge-small-en-v1.5 (384-dim)"),
        ("LLM Provider", "Groq (llama-3.1-70b-versatile)"),
    ]
    
    for label, value in app_info:
        st.markdown(f"""
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--color-divider);">
            <span style="font-weight: 500; color: var(--color-text-secondary);">{label}</span>
            <span style="font-family: var(--font-family-mono); font-size: 0.875rem; color: var(--color-text-primary);">{value}</span>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # Data Management
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Data Management</h3>
            <p class="card-subtitle">Storage and maintenance options</p>
        </div>
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**Vector Database**")
        st.caption("Location: ./storage/chroma/")
        st.caption("Collection: telecom_manuals")
        if st.button("📊 View Collection Stats", use_container_width=True, key="settings_view_stats"):
            result = call_api("/health")
            if "error" not in result:
                st.json({
                    "documents": result.get("document_count", 0),
                    "embedding_model": result.get("embedding_model", "unknown"),
                    "llm_model": result.get("llm_model", "unknown"),
                })
    
    with col2:
        st.markdown("**Document Storage**")
        st.caption("Location: ./data/manuals/")
        if st.button("🗂️ Open Data Folder", use_container_width=True, key="settings_open_data"):
            import os
            os.startfile("./data/manuals") if os.name == 'nt' else st.info("Open ./data/manuals/ in file explorer")
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Danger zone
    st.markdown("""
    <div style="border: 1px solid var(--color-error); border-radius: var(--radius-md); padding: 16px; background: var(--color-error-bg);">
        <h4 style="margin: 0 0 12px; color: var(--color-error);">⚠️ Danger Zone</h4>
        <p style="margin: 0 0 16px; color: var(--color-text-secondary); font-size: 0.875rem;">
            These actions are irreversible. Use with caution.
        </p>
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🗑️ Clear All Chat Sessions", use_container_width=True, type="secondary", key="settings_clear_sessions"):
            from frontend.components import clear_all_sessions, clear_session_history
            result = clear_all_sessions()
            if "error" not in result:
                clear_session_history()
                st.success("All chat sessions cleared")
                st.rerun()
            else:
                st.error(f"Failed: {result['error']}")
    
    with col2:
        if st.button("🗑️ Delete Vector Database", use_container_width=True, type="secondary", key="settings_delete_vectordb"):
            st.warning("This would delete all indexed vectors. Implementation requires backend endpoint.")
    
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    render_settings_page()