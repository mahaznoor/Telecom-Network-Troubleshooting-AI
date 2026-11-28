"""AI Troubleshooting Assistant page - Premium chat interface."""

from html import escape
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
    submit_response_feedback,
)


SUGGESTED_QUESTIONS = [
    "Why is my ONT LOS indicator blinking?",
    "How can I troubleshoot an OLT port issue?",
    "Explain common 5G connectivity failures.",
    "What causes high optical attenuation?",
    "How can I diagnose fiber link loss?",
]


def format_structured_answer(answer: str) -> str:
    """Normalize structured section labels while leaving general replies alone."""
    heading_aliases = {
        "Problem Summary": "Issue Summary",
        "Diagnostic Steps": "Step-by-Step Troubleshooting",
        "Recommended Actions": "Recommended Solution",
        "Source References": "Sources / References",
    }
    valid_headings = {
        "Issue Summary",
        "Possible Causes",
        "Step-by-Step Troubleshooting",
        "Recommended Solution",
        "Verification Steps",
        "Sources / References",
    }
    formatted_lines = []
    for line in answer.strip().splitlines():
        candidate = line.strip().lstrip("#").strip().strip("*").strip().rstrip(":").strip()
        heading = heading_aliases.get(candidate, candidate)
        if heading in valid_headings:
            formatted_lines.append(f"## {heading}")
        else:
            formatted_lines.append(line)
    return "\n".join(formatted_lines)


def render_source_citations(sources: list):
    """Render source citations in a professional panel."""
    if not sources:
        return
    
    st.markdown("### 📚 Source References")
    
    for i, source in enumerate(sources, 1):
        source_file = escape(str(source.get("source_file", "Unknown")))
        page_number = escape(str(source.get("page_number", "Unknown")))
        preview = escape(
            str(source.get("content_preview", "No preview available"))
        )
        
        st.markdown(f"""
        <div class="source-citation">
            <div class="source-citation-header">
                <span>Source {i}: {source_file}</span>
                <span class="badge badge-primary">Page {page_number}</span>
            </div>
            <div class="source-citation-content">{preview}</div>
        </div>
        """, unsafe_allow_html=True)


def render_response_feedback(session_id: str, message_id: int) -> None:
    """Render a rating form tied to one persisted assistant response."""
    rating_key = f"feedback_rating_{session_id}_{message_id}"
    comment_key = f"feedback_comment_{session_id}_{message_id}"
    form_key = f"feedback_form_{session_id}_{message_id}"

    st.caption("Rate this response: 👍 Helpful · 👎 Not helpful")
    with st.form(key=form_key, border=False):
        rating = st.feedback(
            "thumbs",
            key=rating_key,
            persist_state="session",
        )
        comment = st.text_area(
            "Optional comment (maximum 1000 characters)",
            key=comment_key,
            max_chars=1000,
            placeholder="What was helpful or what could be improved?",
            help=(
                "Do not include API keys, passwords, or other sensitive information. "
                "When updating a rating, leaving this blank keeps any saved comment."
            ),
            persist_state="session",
        )
        submitted = st.form_submit_button(
            "Submit feedback",
            key=f"feedback_submit_{session_id}_{message_id}",
        )

    if not submitted:
        return
    if rating is None:
        st.warning("Choose Helpful or Not helpful before submitting feedback.")
        return

    result = submit_response_feedback(
        session_id=session_id,
        message_id=message_id,
        rating="helpful" if rating == 1 else "not_helpful",
        comment=comment.strip() or None,
    )
    if result.get("error"):
        st.error(result["error"])
    else:
        st.success("Feedback saved. Submit again to change your rating or comment.")


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
        
        response_session_id = result.get("session_id")
        if response_session_id:
            st.session_state.session_id = response_session_id

        if result.get("error"):
            error_msg = f"❌ **Error:** {result.get('answer') or result['error']}"
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
            
            add_message(
                "assistant",
                formatted_answer,
                sources,
                message_id=result.get("message_id"),
            )
    st.rerun()


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
                    st.markdown(format_structured_answer(message["content"]))
                    if message.get("sources"):
                        render_source_citations(message["sources"])
                    message_id = message.get("message_id")
                    if message["role"] == "assistant" and isinstance(message_id, int):
                        render_response_feedback(get_session_id(), message_id)
    
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
        <h1 style="margin: 0 0 8px;">AI Troubleshooting Assistant</h1>
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
            if get_chat_history():
                result = clear_session(get_session_id())
                if result.get("error"):
                    st.error(result["error"])
                else:
                    clear_session_history()
                    st.rerun()
            else:
                clear_session_history()
                st.rerun()
    
    st.divider()
    
    # Main chat interface
    render_chat_interface()


if __name__ == "__main__":
    render_ai_assistant_page()