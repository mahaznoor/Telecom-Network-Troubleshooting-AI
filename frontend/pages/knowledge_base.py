"""Knowledge Base page - Document management interface."""

import streamlit as st
from backend.config import settings
from frontend.components import (
    init_app_session_state,
    ingest_documents,
    upload_documents,
    get_ingestion_status,
    get_documents_list,
    set_ingestion_status,
    is_current_user_admin,
    delete_indexed_document,
)
from frontend.components.document_metadata import (
    build_document_rows,
    filter_documents,
    format_file_size,
)


def render_document_upload():
    """Render document upload section."""
    if not is_current_user_admin():
        st.info("Document ingestion is available to administrators only.")
        return
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Upload Documents</h3>
            <p class="card-subtitle">Add new telecom manuals to the knowledge base</p>
        </div>
    """, unsafe_allow_html=True)
    
    # Two upload methods
    tab1, tab2 = st.tabs(["📁 From server folder", "📤 Upload files"])
    
    with tab1:
        st.markdown("""
        <div style="padding: 8px 0;">
            <p style="color: var(--color-text-secondary); margin-bottom: 16px;">
                Ingest all PDF files from the configured <code>DATA_DIR</code> on the server.
            </p>
        </div>
        """, unsafe_allow_html=True)
        
        col1, col2 = st.columns([3, 1])
        with col1:
            if st.button("📥 Ingest Default Folder", use_container_width=True, type="primary", key="kb_ingest_folder"):
                with st.spinner("Ingesting documents from DATA_DIR..."):
                    result = ingest_documents(directory=settings.DATA_DIR)
                    set_ingestion_status(result)
                    if result.get("status") == "success":
                        st.success(
                            f"✅ Ingested {result.get('documents_processed', 0)} "
                            f"documents ({result.get('chunks_created', 0)} chunks)"
                        )
                        if result.get("message"):
                            st.info(result["message"])
                        st.rerun()
                    elif result.get("status") == "warning":
                        st.warning(result.get("message", "No documents were ingested."))
                    else:
                        st.error(
                            result.get("message")
                            or result.get("error")
                            or "Ingestion failed."
                        )
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
                    result = upload_documents(uploaded_files)
                    
                    set_ingestion_status(result)
                    if result.get("status") == "success":
                        st.success(
                            f"✅ Ingested {result.get('documents_processed', 0)} "
                            f"documents ({result.get('chunks_created', 0)} chunks)"
                        )
                        if result.get("message"):
                            st.info(result["message"])
                        st.rerun()
                    elif result.get("status") == "warning":
                        st.warning(result.get("message", "No documents were ingested."))
                    else:
                        error_msg = result.get("message") or result.get("error") or "Ingestion failed"
                        st.error(f"❌ {error_msg}")
    
    # Show last ingestion status
    ingestion = get_ingestion_status()
    if ingestion:
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
    
    result = get_documents_list()
    if result.get("error"):
        st.error(result["error"], title="Could not load indexed documents")
    else:
        documents = result.get("documents", [])
        success_message = st.session_state.pop("kb_delete_success", None)
        if success_message:
            st.success(success_message)
        if not documents:
            st.markdown("""
            <div class="empty-state">
                <div class="empty-state-icon">📚</div>
                <h4 class="empty-state-title">No Documents Indexed</h4>
                <p class="empty-state-description">
                    Upload PDF manuals using the upload section above to build your knowledge base.
                </p>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.caption(f"{len(documents)} indexed document(s)")
            query = st.text_input(
                "Filter indexed documents",
                placeholder="Search by file name",
                type="search",
                key="kb_document_filter",
            )
            matching_documents = filter_documents(documents, query)
            if matching_documents:
                st.dataframe(
                    build_document_rows(matching_documents),
                    hide_index=True,
                )
            else:
                st.info("No indexed documents match that file name.")

        if is_current_user_admin():
            deletable_documents = [
                document
                for document in documents
                if isinstance(document.get("document_id"), str)
            ]
            st.markdown("### Remove a document from the index")
            st.caption(
                "This removes indexed chunks only. The original PDF remains "
                "in server storage."
            )
            if not deletable_documents:
                st.info(
                    "No indexed documents with a removable content fingerprint "
                    "are available."
                )
            else:
                documents_by_id = {
                    document["document_id"]: document
                    for document in deletable_documents
                }
                selected_id = st.selectbox(
                    "Indexed document",
                    options=list(documents_by_id),
                    format_func=lambda document_id: (
                        f"{documents_by_id[document_id]['filename']} "
                        f"({document_id[:12]}...)"
                    ),
                    key="kb_selected_document_id",
                )
                if st.button(
                    "Remove selected document from index",
                    key="kb_request_document_deletion",
                ):
                    st.session_state["kb_pending_delete_id"] = selected_id

                pending_id = st.session_state.get("kb_pending_delete_id")
                pending_document = documents_by_id.get(pending_id)
                if pending_document is not None:
                    st.warning(
                        f"Confirm removal of {pending_document['filename']} "
                        "from the shared knowledge-base index."
                    )
                    confirm_col, cancel_col = st.columns(2)
                    with confirm_col:
                        if st.button(
                            "Confirm index removal",
                            key="kb_confirm_document_deletion",
                            type="primary",
                        ):
                            deletion = delete_indexed_document(pending_id)
                            if (
                                deletion.get("error")
                                or deletion.get("status") != "success"
                            ):
                                st.error(
                                    deletion.get("error")
                                    or deletion.get(
                                        "message",
                                        "The document could not be removed.",
                                    )
                                )
                            else:
                                st.session_state.pop(
                                    "kb_pending_delete_id",
                                    None,
                                )
                                st.session_state["kb_delete_success"] = (
                                    deletion.get("message")
                                    or "The document was removed from the index."
                                )
                                st.rerun()
                    with cancel_col:
                        if st.button(
                            "Cancel",
                            key="kb_cancel_document_deletion",
                        ):
                            st.session_state.pop("kb_pending_delete_id", None)
                            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


def render_knowledge_base_page():
    """Main Knowledge Base page renderer."""
    init_app_session_state()
    
    # Page header
    st.markdown("""
    <div style="margin-bottom: 24px;">
        <h1 style="margin: 0 0 8px;">Knowledge Base</h1>
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