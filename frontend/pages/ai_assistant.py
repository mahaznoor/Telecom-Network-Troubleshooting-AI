"""AI Troubleshooting Assistant page - Premium chat interface."""

import streamlit as st
from frontend.components import (
    init_app_session_state,
    chat_question,
    get_session_id,
    new_session,
    clear_session_history,
    get_chat_history,
    add_message,
    call_api,
    set_health_status,
)


SUGGESTED_QUESTIONS = [
    "Why is my ONT LOS indicator blinking?",
    "How can I troubleshoot an OLT port issue?",
    "Explain common 5G connectivity failures.",
    "What causes high optical attenuation?",
    "How can I diagnose fiber link loss?",
]


def format_structured_answer(answer: str) -> str:
    """Format the AI answer into structured sections if not already formatted."""
    # Check if answer already has structured sections
    sections = [
        "Problem Summary",
        "Possible Causes",
        "Diagnostic Steps",
        "Recommended Actions",
        "Safety Precautions",
        "Source References",
    ]
    
    has_structure = any(section.lower() in answer.lower() for section in sections)
    
    if has_structure:
        # Already structured, just return
        return answer
    
    # Not structured, return as-is with basic formatting
    return answer


def render_source_citations(sources: list):
    """Render source citations in a professional panel."""
    if not sources:
        return
    
    st.markdown("### 📚 Source References")
    
    for i, source in enumerate(sources, 1):
        source_file = source.get("source_file", "Unknown")
        page_number = source.get("page_number", "Unknown")
        preview = source.get("content_preview", "No preview available")
        
        st.markdown(f"""
        <div class="source-citation">
            <div class="source-citation-header">
                <span>Source {i}: {source_file}</span>
                <span class="badge badge-primary">Page {page_number}</span>
            </div>
            <div class="source-citation-content">{preview}</div>
        </div>
        """, unsafe_allow_html=True)


def render_suggested_questions():
    """Render suggested questions as clickable chips."""
    st.markdown("#### Suggested Questions")
    st.caption("Click any question to start troubleshooting")
    
    cols = st.columns(2)
    for idx, question in enumerate(SUGGESTED_QUESTIONS):
        with cols[idx % 2]:
            if st.button(
                question,
                key=f"suggested_{idx}",
                use_container_width=True,
                type="secondary"
            ):
                # Add user message and get AI response
                handle_user_question(question)


def handle_user_question(question: str):
    """Process a user question and display AI response."""
    session_id = get_session_id()
    top_k = st.session_state.get("top_k", 5)
    similarity_threshold = st.session_state.get("similarity_threshold", None)
    use_mmr = st.session_state.get("use_mmr", None)
    
    # Add user message
    add_message("user", question)
    
    # Show loading and get response
    with st.chat_message("assistant"):
        with st.spinner("Analyzing telecom manuals..."):
            result = chat_question(
                question,
                session_id,
                top_k,
                similarity_threshold=similarity_threshold,
                use_mmr=use_mmr,
            )
        
        if "error" in result:
            error_msg = f"❌ **Error:** {result['error']}"
            st.error(error_msg)
            add_message("assistant", error_msg)
        else:
            answer = result.get("answer", "No answer generated.")
            sources = result.get("sources", [])
            
            # Display formatted answer
            formatted_answer = format_structured_answer(answer)
            st.markdown(formatted_answer)
            
            # Display sources
            if sources:
                render_source_citations(sources)
            
            add_message("assistant", formatted_answer, sources)


def render_chat_interface():
    """Render the main chat interface."""
    # Chat history container
    chat_container = st.container()
    
    with chat_container:
        messages = get_chat_history()
        
        if not messages:
            # Empty state with suggested questions
            st.markdown("""
            <div class="empty-state" style="margin: 48px 0;">
                <div class="empty-state-icon">🤖</div>
                <h4 class="empty-state-title">Welcome to AI Troubleshooting Assistant</h4>
                <p class="empty-state-description">
                    Ask me any telecom network troubleshooting question. I'll search your 
                    uploaded technical manuals and provide structured answers with citations.
                </p>
            </div>
            """, unsafe_allow_html=True)
            
            render_suggested_questions()
        else:
            # Display chat history
            for message in messages:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])
                    if message.get("sources"):
                        render_source_citations(message["sources"])
    
    # Chat input (fixed at bottom)
    st.markdown("<div style='height: 20px;'></div>", unsafe_allow_html=True)
    
    if prompt := st.chat_input(
        "Describe the network issue or ask a troubleshooting question...",
        key="chat_input_main"
    ):
        handle_user_question(prompt)


def render_ai_assistant_page():
    """Main AI Assistant page renderer."""
    init_app_session_state()
    
    # Page header
    st.markdown("""
    <div style="margin-bottom: 24px;">
        <h1 style="margin: 0 0 8px;">🤖 AI Troubleshooting Assistant</h1>
        <p style="color: var(--color-text-secondary); margin: 0; font-size: 1.125rem;">
            Intelligent telecom network diagnosis powered by your technical manuals.
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    # Session controls
    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        st.caption(f"Session: `{get_session_id()[:8]}...`")
    with col2:
        if st.button("🔄 New Chat", use_container_width=True, key="ai_new_chat"):
            new_session()
            st.rerun()
    with col3:
        if st.button("🗑️ Clear History", use_container_width=True, key="ai_clear_history"):
            clear_session_history()
            st.rerun()
    
    st.divider()
    
    # Main chat interface
    render_chat_interface()


if __name__ == "__main__":
    render_ai_assistant_page()