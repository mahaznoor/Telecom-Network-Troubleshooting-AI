"""Knowledge Base page - Document management interface."""

import streamlit as st
import os
import tempfile
from datetime import datetime
from frontend.components import (
    init_app_session_state,
    ingest_documents,
    get_ingestion_status,
    call_api,
    set_ingestion_status,
)


def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def render_document_upload():
    """Render document upload section."""
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Upload Documents</h3>
            <p class="card-subtitle">Add new telecom manuals to the knowledge base</p>
        </div>
    """, unsafe_allow_html=True)
    
    # Two upload methods
    tab1, tab2 = st.tabs(["📁 From data/manuals/", "📤 Upload Files"])
    
    with tab1:
        st.markdown("""
        <div style="padding: 8px 0;">
            <p style="color: var(--color-text-secondary); margin-bottom: 16px;">
                Ingest all PDF files from the <code>data/manuals/</code> directory on the server.
            </p>
        </div>
        """, unsafe_allow_html=True)
        
        col1, col2 = st.columns([3, 1])
        with col1:
            if st.button("📥 Ingest Default Folder", use_container_width=True, type="primary", key="kb_ingest_folder"):
                with st.spinner("Ingesting documents from data/manuals/..."):
                    result = ingest_documents(directory="./data/manuals")
                    set_ingestion_status(result)
                    if result.get("status") == "success":
                        st.success(f"✅ Ingested {result['documents_processed']} documents ({result['chunks_created']} chunks)")
                        st.rerun()
                    else:
                        st.error(f"❌ {result.get('message', 'Ingestion failed')}")
        with col2:
            if st.button("🔄 Refresh", use_container_width=True, key="kb_refresh_folder"):
                st.rerun()
    
    with tab2:
        st.markdown("""
        <div style="padding: 8px 0;">
            <p style="color: var(--color-text-secondary); margin-bottom: 16px;">
                Upload one or more PDF files from your computer.
            </p>
        </div>
        """, unsafe_allow_html=True)
        
        uploaded_files = st.file_uploader(
            "Choose PDF files",
            type=["pdf"],
            accept_multiple_files=True,
            label_visibility="collapsed",
            key="kb_file_uploader"
        )
        
        if uploaded_files:
            st.markdown(f"**{len(uploaded_files)} file(s) selected:**")
            for f in uploaded_files:
                st.caption(f"📄 {f.name} ({format_file_size(f.size)})")
            
            if st.button("📤 Ingest Uploaded Files", use_container_width=True, type="primary", key="kb_ingest_upload"):
                with st.spinner("Uploading and ingesting..."):
                    file_paths = []
                    for uploaded_file in uploaded_files:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                            tmp.write(uploaded_file.getvalue())
                            file_paths.append(tmp.name)
                    
                    result = ingest_documents(file_paths=file_paths)
                    
                    # Cleanup temp files
                    for path in file_paths:
                        try:
                            os.unlink(path)
                        except:
                            pass
                    
                    set_ingestion_status(result)
                    if result.get("status") == "success":
                        st.success(f"✅ Ingested {result['documents_processed']} documents ({result['chunks_created']} chunks)")
                        st.rerun()
                    else:
                        error_msg = result.get("message") or result.get("error") or "Ingestion failed"
                        st.error(f"❌ {error_msg}")
    
    # Show last ingestion status
    ingestion = get_ingestion_status()
    if ingestion and ingestion.get("status") == "success":
        with st.expander("📋 Last Ingestion Details", expanded=False):
            st.json(ingestion)
    
    st.markdown("</div>", unsafe_allow_html=True)


def render_document_table():
    """Render document list table."""
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Indexed Documents</h3>
            <p class="card-subtitle">All documents currently in the knowledge base</p>
        </div>
    """, unsafe_allow_html=True)
    
    # Get document stats from health
    health = call_api("/health")
    
    if health and "error" not in health and health.get("document_count", 0) > 0:
        doc_count = health.get("document_count", 0)
        
        # Show document info
        st.markdown(f"""
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
            <div>
                <span style="font-weight: 500; color: var(--color-text-primary);">{doc_count}</span> document(s) indexed
            </div>
            <span class="badge badge-success">{health.get('embedding_model', 'BAAI/bge-small-en-v1.5')}</span>
        </div>
        """, unsafe_allow_html=True)
        
        # Try to get more details from vector store
        # For now, show what we have
        st.info("📋 Document listing from ChromaDB requires additional API endpoint. Current count shown above.")
        
        # Placeholder for future document table
        st.markdown("""
        <div style="margin-top: 16px; padding: 16px; background: var(--color-bg-secondary); border-radius: var(--radius-md);">
            <div style="display: flex; align-items: center; gap: 12px; color: var(--color-text-muted);">
                <span>🔧</span>
                <span>Document table with file size, page count, chunk count, and upload timestamp will be available after implementing document metadata storage in the backend.</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        # Empty state
        st.markdown("""
        <div class="empty-state">
            <div class="empty-state-icon">📚</div>
            <h4 class="empty-state-title">No Documents Indexed</h4>
            <p class="empty-state-description">
                Upload PDF manuals using the upload section above to build your knowledge base.
            </p>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("</div>", unsafe_allow_html=True)


def render_knowledge_base_page():
    """Main Knowledge Base page renderer."""
    init_app_session_state()
    
    # Page header
    st.markdown("""
    <div style="margin-bottom: 24px;">
        <h1 style="margin: 0 0 8px;">📚 Knowledge Base</h1>
        <p style="color: var(--color-text-secondary); margin: 0; font-size: 1.125rem;">
            Manage your telecom technical documentation and manuals.
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    # Upload section
    render_document_upload()
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Document table
    render_document_table()


if __name__ == "__main__":
    render_knowledge_base_page()