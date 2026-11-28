"""Network Documentation page - Documentation explorer."""

import streamlit as st
from frontend.components import (
    init_app_session_state,
    search_documents,
    get_documents_list,
)
from frontend.components.document_metadata import build_document_rows


DOC_CATEGORIES = [
    {"key": "4g_lte", "label": "4G & LTE", "icon": "📶", "keywords": ["4g", "lte", "evolved packet core", "epc", "ims"]},
    {"key": "5g", "label": "5G Networks", "icon": "📡", "keywords": ["5g", "nr", "new radio", "5g core", "5gc"]},
    {"key": "fiber", "label": "Fiber Optics", "icon": "🔬", "keywords": ["fiber", "optical", "attenuation", "otdr", "splicing"]},
    {"key": "gpon", "label": "GPON", "icon": "🌐", "keywords": ["gpon", "gpon", "olt", "ont", "onu"]},
    {"key": "olt_ont", "label": "OLT & ONT", "icon": "📦", "keywords": ["olt", "ont", "onu", "gpon", "epon"]},
    {"key": "transmission", "label": "Optical Transmission", "icon": "📊", "keywords": ["dwdm", "cwdm", "otn", "sdh", "sonet"]},
    {"key": "routing", "label": "Routers & Switching", "icon": "🔀", "keywords": ["router", "switch", "bgp", "ospf", "isis", "mpls"]},
    {"key": "troubleshooting", "label": "Network Troubleshooting", "icon": "🔧", "keywords": ["troubleshoot", "debug", "fault", "alarm", "recovery"]},
]


def categorize_document(filename: str, content_preview: str = "") -> list:
    """Categorize a document based on filename and content."""
    text = (filename + " " + content_preview).lower()
    categories = []
    for cat in DOC_CATEGORIES:
        if any(kw in text for kw in cat["keywords"]):
            categories.append(cat["key"])
    return categories if categories else ["troubleshooting"]


def render_category_card(category: dict, doc_count: int):
    """Render a category card."""
    st.markdown(f"""
    <div class="card" style="cursor: pointer; height: 100%;" onclick="this.style.transform='scale(0.98)'; setTimeout(() => this.style.transform='', 150)">
        <div style="display: flex; flex-direction: column; align-items: center; text-align: center; padding: 24px;">
            <div style="font-size: 2.5rem; margin-bottom: 12px;">{category['icon']}</div>
            <h4 style="margin: 0 0 8px; font-size: 1.125rem; font-weight: 600; color: var(--color-text-primary);">
                {category['label']}
            </h4>
            <div style="font-size: 1.5rem; font-weight: 700; color: var(--color-primary);">{doc_count}</div>
            <div style="font-size: 0.75rem; color: var(--color-text-muted); margin-top: 4px;">document(s)</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_document_search():
    """Render document search interface."""
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Search Documentation</h3>
            <p class="card-subtitle">Search across all indexed documents</p>
        </div>
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([4, 1])
    with col1:
        search_query = st.text_input(
            "Search",
            placeholder="Enter keywords (e.g., BGP configuration, ONT registration, fiber attenuation)...",
            label_visibility="collapsed",
            key="nd_search_input"
        )
    with col2:
        search_btn = st.button("🔍 Search", use_container_width=True, type="primary", key="nd_search_btn")
    
    if search_query and search_btn:
        with st.spinner("Searching documents..."):
            result = search_documents(
                query=search_query,
                top_k=10,
                use_mmr=True,
            )
        
        if not result.get("error"):
            st.markdown("### Search Results")
            sources = result.get("results", [])
            if sources:
                for i, source in enumerate(sources, 1):
                    score = source.get("similarity_score")
                    score_text = f" (score: {score:.3f})" if score is not None else ""
                    with st.expander(f"📄 {source.get('source_file', 'Unknown')} - Page {source.get('page_number', 'Unknown')}{score_text}", expanded=False):
                        st.markdown(f"**Source:** {source.get('source_file', 'Unknown')}")
                        st.markdown(f"**Page:** {source.get('page_number', 'Unknown')}")
                        st.markdown(f"**Preview:** {source.get('content_preview', 'No preview')}")
            else:
                st.info("No matching documents found. Try different keywords.")
        else:
            error_msg = result.get("error", "Unknown error")
            if "connection" in error_msg.lower() or "Cannot connect" in error_msg:
                st.error("🔌 **Search failed:** Cannot connect to the backend. Is the API server running?")
            elif "configuration" in error_msg.lower() or "api key" in error_msg.lower():
                st.error("⚙️ **Search failed:** Backend is not configured. GROQ_API_KEY may be missing or invalid.")
            else:
                st.error(f"🔍 **Search failed:** {error_msg}")
    
    st.markdown("</div>", unsafe_allow_html=True)


def render_network_docs_page():
    """Main Network Documentation page renderer."""
    init_app_session_state()
    
    # Page header
    st.markdown("""
    <div style="margin-bottom: 24px;">
        <h1 style="margin: 0 0 8px;">Network Documentation</h1>
        <p style="color: var(--color-text-secondary); margin: 0; font-size: 1.125rem;">
            Browse and search telecom technical documentation organized by category.
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    # Search section
    render_document_search()
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Categories
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Documentation Categories</h3>
            <p class="card-subtitle">Browse documents by technology domain</p>
        </div>
    """, unsafe_allow_html=True)
    
    # Get real document data from backend
    docs_result = get_documents_list()
    documents_error = docs_result.get("error")
    documents = docs_result.get("documents", []) if not documents_error else []
    total_docs = len(documents)
    if documents_error:
        st.error(documents_error, title="Could not load indexed documents")
    
    if documents_error:
        st.info("Documentation categories are unavailable until the backend responds.")
    elif total_docs > 0:
        category_counts = {category["key"]: 0 for category in DOC_CATEGORIES}
        for document in documents:
            for category_key in categorize_document(str(document.get("filename") or "")):
                category_counts[category_key] += 1

        cols = st.columns(4)
        for idx, category in enumerate(DOC_CATEGORIES):
            with cols[idx % 4]:
                render_category_card(category, category_counts[category["key"]])
    else:
        st.markdown("""
        <div class="empty-state">
            <div class="empty-state-icon">🗂️</div>
            <h4 class="empty-state-title">No Documentation Available</h4>
            <p class="empty-state-description">
                Upload PDF manuals in the Knowledge Base to populate documentation categories.
            </p>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # Recent documents section
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Recently Added</h3>
            <p class="card-subtitle">Latest documents added to the knowledge base</p>
        </div>
    """, unsafe_allow_html=True)
    
    if documents_error:
        st.caption("Recent document information is unavailable until the backend responds.")
    elif total_docs > 0:
        recent_documents = sorted(
            documents,
            key=lambda document: document.get("ingested_at") or "",
            reverse=True,
        )[:5]
        st.dataframe(build_document_rows(recent_documents), hide_index=True)
    else:
        st.markdown("""
        <div class="empty-state" style="padding: 32px 16px;">
            <div class="empty-state-icon">📄</div>
            <h4 class="empty-state-title">No Recent Documents</h4>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    render_network_docs_page()