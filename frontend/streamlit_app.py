"""Streamlit frontend for Telecom Network Troubleshooting AI."""

import streamlit as st
import requests
import uuid
import os
from typing import List, Dict, Any


# Configuration
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")


def init_session_state():
    """Initialize Streamlit session state."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "ingestion_status" not in st.session_state:
        st.session_state.ingestion_status = None
    if "health_status" not in st.session_state:
        st.session_state.health_status = None


def call_api(endpoint: str, method: str = "GET", data: Dict = None, timeout: int = 60) -> Dict[str, Any]:
    """Call the backend API."""
    url = f"{API_BASE_URL}{endpoint}"
    try:
        if method == "GET":
            response = requests.get(url, timeout=timeout)
        elif method == "POST":
            response = requests.post(url, json=data, timeout=timeout)
        elif method == "DELETE":
            response = requests.delete(url, timeout=timeout)
        else:
            return {"error": "Invalid method"}

        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        return {"error": "Cannot connect to backend. Is the API server running?"}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out. The backend may be processing a large request."}
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def display_chat_message(role: str, content: str, sources: List[Dict] = None):
    """Display a chat message with optional sources."""
    with st.chat_message(role):
        st.markdown(content)
        if sources and role == "assistant":
            with st.expander("📚 Sources", expanded=False):
                for i, source in enumerate(sources, 1):
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.markdown(f"**Source {i}:** {source.get('source_file', 'Unknown')}")
                    with col2:
                        page = source.get('page_number', 'Unknown')
                        st.markdown(f"**Page:** {page}")
                    preview = source.get('content_preview', 'No preview')
                    st.markdown(f"> {preview}")
                    if i < len(sources):
                        st.divider()


def render_sidebar():
    """Render the sidebar with system health and controls."""
    with st.sidebar:
        st.markdown("""
        <div style='text-align: center; padding: 1rem 0;'>
            <h2>📡 Telecom AI</h2>
            <p style='color: #666;'>Network Troubleshooting Assistant</p>
        </div>
        """, unsafe_allow_html=True)
        st.divider()

        # System Health
        st.subheader("🏥 System Health")
        if st.button("🔄 Refresh Health", use_container_width=True):
            st.session_state.health_status = call_api("/health")
        
        if st.session_state.health_status:
            health = st.session_state.health_status
            if "error" not in health:
                # Status indicator
                status_color = "🟢" if health.get("status") == "healthy" else "🟡"
                st.markdown(f"**Status:** {status_color} {health.get('status', 'unknown').title()}")
                
                # Groq config
                groq_status = "🟢 Connected" if health.get("groq_configured") else "🔴 Not Configured"
                st.markdown(f"**Groq API:** {groq_status}")
                
                # Vector DB
                db_status = "🟢 Ready" if health.get("vector_db_ready") else "🟡 Empty"
                st.markdown(f"**Vector DB:** {db_status} ({health.get('document_count', 0)} docs)")
                
                st.caption(f"LLM: {health.get('llm_model', 'unknown')}")
                st.caption(f"Embeddings: {health.get('embedding_model', 'unknown')}")
            else:
                st.error(f"Health check failed: {health['error']}")
        else:
            st.info("Click 'Refresh Health' to check system status")

        st.divider()

        # Document Ingestion
        st.subheader("📄 Document Ingestion")
        
        # Option 1: Ingest from data/manuals/
        st.markdown("**From data/manuals/ folder:**")
        if st.button("📥 Ingest Default Folder", use_container_width=True):
            with st.spinner("Ingesting documents from data/manuals/..."):
                result = call_api("/ingest", "POST", {"directory": "./data/manuals"})
                st.session_state.ingestion_status = result
                if result.get("status") == "success":
                    st.success(f"✅ Ingested {result['documents_processed']} documents ({result['chunks_created']} chunks)")
                else:
                    st.error(f"❌ {result.get('message', 'Ingestion failed')}")
        
        st.markdown("---")
        
        # Option 2: Upload files
        st.markdown("**Upload PDF files:**")
        uploaded_files = st.file_uploader(
            "Choose PDF files",
            type=["pdf"],
            accept_multiple_files=True,
            label_visibility="collapsed"
        )
        
        if uploaded_files and st.button("📤 Ingest Uploaded Files", use_container_width=True):
            with st.spinner("Uploading and ingesting..."):
                import tempfile
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
                    st.success(f"✅ Ingested {result['documents_processed']} documents ({result['chunks_created']} chunks)")
                else:
                    st.error(f"❌ {result.get('message', 'Ingestion failed')}")

        if st.session_state.ingestion_status and st.session_state.ingestion_status.get("status") == "success":
            with st.expander("📋 Last Ingestion Details"):
                st.json(st.session_state.ingestion_status)

        st.divider()

        # Session Management
        st.subheader("💬 Session")
        st.caption(f"ID: `{st.session_state.session_id[:8]}...`")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 New Session", use_container_width=True):
                st.session_state.session_id = str(uuid.uuid4())
                st.session_state.messages = []
                st.rerun()
        with col2:
            if st.button("🗑️ Clear History", use_container_width=True):
                result = call_api(f"/chat/{st.session_state.session_id}", "DELETE")
                if "error" not in result:
                    st.session_state.messages = []
                    st.success("History cleared")
                    st.rerun()
                else:
                    st.error(f"Failed: {result['error']}")

        st.divider()

        # Settings
        st.subheader("⚙️ Settings")
        top_k = st.slider("Top K Results", 1, 20, 5, help="Number of document chunks to retrieve")
        st.session_state.top_k = top_k
        
        use_mmr = st.checkbox("Use MMR (diverse results)", value=True, help="Maximum Marginal Relevance for diverse results")
        st.session_state.use_mmr = use_mmr


def main():
    """Main Streamlit application."""
    st.set_page_config(
        page_title="Telecom Network Troubleshooting AI",
        page_icon="📡",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            'Get Help': None,
            'Report a bug': None,
            'About': "Telecom Network Troubleshooting AI v1.0\nPowered by Groq & LangChain"
        }
    )

    # Custom CSS for professional look
    st.markdown("""
    <style>
    .stApp {
        background-color: #fafafa;
    }
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    .stChatMessage {
        background-color: white;
        border-radius: 0.5rem;
        padding: 1rem;
        margin-bottom: 0.5rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .stChatMessage[data-testid="stChatMessage-user"] {
        border-left: 4px solid #1f77b4;
    }
    .stChatMessage[data-testid="stChatMessage-assistant"] {
        border-left: 4px solid #2ca02c;
    }
    h1 {
        color: #1a1a2e;
        font-weight: 600;
    }
    .stButton > button {
        border-radius: 0.5rem;
        font-weight: 500;
    }
    .stTextInput > div > div > input {
        border-radius: 0.5rem;
    }
    </style>
    """, unsafe_allow_html=True)

    init_session_state()
    render_sidebar()

    # Main Header
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("💬 Network Troubleshooting Assistant")
        st.caption("Ask questions about telecom network issues, configurations, and troubleshooting procedures.")
    with col2:
        if st.session_state.health_status and "error" not in st.session_state.health_status:
            health = st.session_state.health_status
            if health.get("status") == "healthy":
                st.success("🟢 System Ready")
            else:
                st.warning("🟡 Degraded")
        else:
            st.info("⚪ Unknown")

    st.divider()

    # Chat History
    chat_container = st.container()
    with chat_container:
        for message in st.session_state.messages:
            display_chat_message(
                message["role"],
                message["content"],
                message.get("sources")
            )

    # Chat Input
    if prompt := st.chat_input("Describe the network issue or ask a troubleshooting question...", key="chat_input"):
        # Add user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        display_chat_message("user", prompt)

        # Get AI response
        with st.chat_message("assistant"):
            with st.spinner("Analyzing telecom manuals..."):
                result = call_api("/chat", "POST", {
                    "question": prompt,
                    "session_id": st.session_state.session_id,
                    "top_k": st.session_state.get("top_k", 5),
                })

            if "error" in result:
                error_msg = f"❌ **Error:** {result['error']}"
                st.error(error_msg)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": error_msg,
                })
            else:
                answer = result.get("answer", "No answer generated.")
                sources = result.get("sources", [])
                
                # Display answer
                st.markdown(answer)
                
                # Display sources
                if sources:
                    with st.expander("📚 Sources", expanded=False):
                        for i, source in enumerate(sources, 1):
                            col1, col2 = st.columns([3, 1])
                            with col1:
                                st.markdown(f"**Source {i}:** {source.get('source_file', 'Unknown')}")
                            with col2:
                                page = source.get('page_number', 'Unknown')
                                st.markdown(f"**Page:** {page}")
                            preview = source.get('content_preview', 'No preview')
                            st.markdown(f"> {preview}")
                            if i < len(sources):
                                st.divider()
                
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer,
                    "sources": sources,
                })


if __name__ == "__main__":
    main()