"""Streamlit frontend for Telecom Network Troubleshooting AI."""

import streamlit as st
import requests
import uuid
import json
from typing import List, Dict, Any


# Configuration
API_BASE_URL = "http://localhost:8000/api/v1"


def init_session_state():
    """Initialize Streamlit session state."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "ingestion_status" not in st.session_state:
        st.session_state.ingestion_status = None


def call_api(endpoint: str, method: str = "GET", data: Dict = None) -> Dict[str, Any]:
    """Call the backend API."""
    url = f"{API_BASE_URL}{endpoint}"
    try:
        if method == "GET":
            response = requests.get(url, timeout=30)
        elif method == "POST":
            response = requests.post(url, json=data, timeout=60)
        elif method == "DELETE":
            response = requests.delete(url, timeout=30)
        else:
            return {"error": "Invalid method"}

        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def display_chat_message(role: str, content: str, sources: List[Dict] = None):
    """Display a chat message with optional sources."""
    with st.chat_message(role):
        st.markdown(content)
        if sources and role == "assistant":
            with st.expander("📚 Sources"):
                for i, source in enumerate(sources, 1):
                    st.markdown(f"**Source {i}:** {source.get('source_file', 'Unknown')}")
                    st.markdown(f"> {source.get('content_preview', 'No preview')}")
                    st.divider()


def main():
    """Main Streamlit application."""
    st.set_page_config(
        page_title="Telecom Network Troubleshooting AI",
        page_icon="📡",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    init_session_state()

    # Sidebar
    with st.sidebar:
        st.title("📡 Telecom Troubleshooting AI")
        st.markdown("---")

        # Session info
        st.markdown(f"**Session ID:** `{st.session_state.session_id[:8]}...`")

        if st.button("🔄 New Session"):
            st.session_state.session_id = str(uuid.uuid4())
            st.session_state.messages = []
            st.rerun()

        st.markdown("---")

        # Document Ingestion
        st.subheader("📄 Document Ingestion")
        uploaded_files = st.file_uploader(
            "Upload PDF manuals",
            type=["pdf"],
            accept_multiple_files=True,
        )

        if st.button("Ingest Documents", type="primary"):
            if uploaded_files:
                with st.spinner("Ingesting documents..."):
                    # Save uploaded files temporarily
                    import tempfile
                    import os
                    file_paths = []
                    for uploaded_file in uploaded_files:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                            tmp.write(uploaded_file.getvalue())
                            file_paths.append(tmp.name)

                    result = call_api("/ingest", "POST", {"file_paths": file_paths})

                    # Cleanup temp files
                    for path in file_paths:
                        try:
                            os.unlink(path)
                        except:
                            pass

                    st.session_state.ingestion_status = result
                    if result.get("status") == "success":
                        st.success(f"Ingested {result['documents_processed']} documents ({result['chunks_created']} chunks)")
                    else:
                        st.error(result.get("message", "Ingestion failed"))
            else:
                st.warning("Please upload PDF files first")

        if st.session_state.ingestion_status:
            st.json(st.session_state.ingestion_status)

        st.markdown("---")

        # Collection Stats
        st.subheader("📊 Collection Stats")
        if st.button("Refresh Stats"):
            stats = call_api("/chat", "POST", {"question": "stats", "session_id": "stats"})
            if "error" not in stats:
                st.json(stats)

        st.markdown("---")

        # Settings
        st.subheader("⚙️ Settings")
        top_k = st.slider("Top K Results", 1, 10, 5)
        st.session_state.top_k = top_k

    # Main Chat Interface
    st.title("💬 Network Troubleshooting Assistant")
    st.markdown("Ask questions about telecom network issues, configurations, and troubleshooting procedures.")

    # Display chat history
    for message in st.session_state.messages:
        display_chat_message(
            message["role"],
            message["content"],
            message.get("sources")
        )

    # Chat input
    if prompt := st.chat_input("Describe the network issue or ask a troubleshooting question..."):
        # Add user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        display_chat_message("user", prompt)

        # Get AI response
        with st.chat_message("assistant"):
            with st.spinner("Analyzing..."):
                result = call_api("/chat", "POST", {
                    "question": prompt,
                    "session_id": st.session_state.session_id,
                    "top_k": st.session_state.get("top_k", 5),
                })

            if "error" in result:
                st.error(f"Error: {result['error']}")
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"Error: {result['error']}",
                })
            else:
                display_chat_message("assistant", result["answer"], result.get("sources"))
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": result["answer"],
                    "sources": result.get("sources", []),
                })


if __name__ == "__main__":
    main()