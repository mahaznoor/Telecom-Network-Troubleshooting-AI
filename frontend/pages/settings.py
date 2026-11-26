"""Settings page - Configuration display and management."""

import streamlit as st
from backend.config import settings
from frontend.components import (
    init_app_session_state,
    get_health_status,
    call_api,
    is_current_user_admin,
)
from frontend.components.health_status import (
    get_groq_key_status,
    get_vector_db_status,
)
from frontend.components.api_client import (
    change_password,
    logout_all_devices,
    list_active_sessions,
    revoke_active_session,
    update_profile,
    request_email_change,
    delete_account,
    get_security_activity,
    delete_vector_database,
)
from frontend.components.session_state import clear_authenticated_session
from frontend.components.api_client import API_BASE_URL


_SECURITY_ACTIVITY_LABELS = {
    "login_success": "Successful sign-in",
    "login_failed": "Unsuccessful sign-in",
    "session_created": "Device session created",
    "session_revoked": "Device session revoked",
    "logout_all_sessions": "Signed out on all devices",
    "password_changed": "Password changed",
    "password_reset_requested": "Password reset requested",
    "password_reset_completed": "Password reset completed",
    "email_change_requested": "Email change requested",
    "email_changed": "Email address changed",
    "account_deletion_requested": "Account deletion requested",
    "account_deleted": "Account deleted",
}


def _submit_password_change() -> None:
    current_password = st.session_state.get("settings_current_password", "")
    new_password = st.session_state.get("settings_new_password", "")
    confirmation = st.session_state.get("settings_confirm_password", "")
    st.session_state["settings_current_password"] = ""
    st.session_state["settings_new_password"] = ""
    st.session_state["settings_confirm_password"] = ""

    st.session_state["settings_security_error"] = None
    st.session_state["settings_security_success"] = None
    if new_password != confirmation:
        st.session_state["settings_security_error"] = (
            "The new password and confirmation do not match."
        )
        return

    result = change_password(
        current_password,
        new_password,
        True,
    )
    if "error" in result:
        st.session_state["settings_security_error"] = result["error"]
        return
    if result.get("access_token"):
        st.session_state["access_token"] = result["access_token"]
        _refresh_active_sessions()
    else:
        clear_authenticated_session()
    st.session_state["settings_security_success"] = result.get(
        "message",
        "Password changed.",
    )


def _refresh_active_sessions() -> None:
    result = list_active_sessions()
    if "error" in result:
        st.session_state["settings_active_sessions"] = None
        st.session_state["settings_sessions_error"] = result["error"]
        return
    st.session_state["settings_active_sessions"] = result.get("sessions", [])
    st.session_state["settings_sessions_error"] = None


def _refresh_security_activity() -> None:
    result = get_security_activity()
    if "error" in result:
        st.session_state["settings_security_activity"] = None
        st.session_state["settings_security_activity_error"] = result["error"]
        return
    st.session_state["settings_security_activity"] = result.get("events", [])
    st.session_state["settings_security_activity_error"] = None


def _revoke_active_session(session_id: str) -> None:
    result = revoke_active_session(session_id)
    if "error" in result:
        st.session_state["settings_sessions_error"] = result["error"]
        st.session_state["settings_sessions_success"] = None
        return
    if result.get("current_session_revoked"):
        clear_authenticated_session()
        st.session_state["auth_success_title"] = "Session revoked"
        st.session_state["auth_success"] = result.get(
            "message",
            "This session is revoked. You are now signed out.",
        )
        return
    st.session_state["settings_sessions_success"] = result.get(
        "message",
        "Session revoked.",
    )
    _refresh_active_sessions()


def _submit_profile_update() -> None:
    name = st.session_state.get("settings_profile_name", "")
    result = update_profile(name)
    if "error" in result:
        st.session_state["settings_account_error"] = result["error"]
        st.session_state["settings_account_success"] = None
        return

    user = dict(st.session_state.get("current_user") or {})
    user["name"] = result.get("name", name.strip())
    st.session_state["current_user"] = user
    st.session_state["settings_profile_name"] = user["name"]
    st.session_state["settings_account_error"] = None
    st.session_state["settings_account_success"] = "Profile updated."


def _submit_email_change() -> None:
    current_password = st.session_state.get(
        "settings_email_current_password",
        "",
    )
    new_email = st.session_state.get("settings_new_email", "")
    st.session_state["settings_email_current_password"] = ""
    result = request_email_change(current_password, new_email)
    if "error" in result:
        st.session_state["settings_account_error"] = result["error"]
        st.session_state["settings_account_success"] = None
        return
    st.session_state["settings_account_error"] = None
    st.session_state["settings_account_success"] = result.get(
        "message",
        "Check the new email address for verification instructions.",
    )


def _submit_account_deletion() -> None:
    current_password = st.session_state.get(
        "settings_delete_current_password",
        "",
    )
    confirmation = st.session_state.get(
        "settings_delete_confirmation",
        "",
    )
    st.session_state["settings_delete_current_password"] = ""
    st.session_state["settings_delete_confirmation"] = ""
    if confirmation != "DELETE MY ACCOUNT":
        st.session_state["settings_delete_error"] = (
            "Type DELETE MY ACCOUNT exactly to confirm."
        )
        return

    result = delete_account(current_password, confirmation)
    if "error" in result:
        st.session_state["settings_delete_error"] = result["error"]
        return

    clear_authenticated_session()
    st.session_state["auth_success_title"] = "Account deleted"
    st.session_state["auth_success"] = result.get(
        "message",
        "Your account has been deleted.",
    )


def _request_logout_all_confirmation() -> None:
    st.session_state["confirm_logout_all_devices"] = True
    st.session_state["settings_security_error"] = None


def _cancel_logout_all() -> None:
    st.session_state["confirm_logout_all_devices"] = False


def _confirm_logout_all() -> None:
    result = logout_all_devices()
    if "error" in result:
        st.session_state["settings_security_error"] = result["error"]
        return

    st.session_state["confirm_logout_all_devices"] = False
    st.session_state["settings_security_error"] = None
    st.session_state["settings_security_success"] = None
    clear_authenticated_session()
    st.session_state["auth_success_title"] = "Signed out everywhere"
    st.session_state["auth_success"] = (
        "All active sessions, including this one, have been signed out."
    )


def _request_vector_database_deletion() -> None:
    st.session_state["settings_confirm_delete_vector"] = True
    st.session_state["settings_vector_delete_error"] = None


def _cancel_vector_database_deletion() -> None:
    st.session_state["settings_confirm_delete_vector"] = False


def _confirm_vector_database_deletion() -> None:
    result = delete_vector_database(confirm=True)
    if "error" in result or result.get("status") != "success":
        st.session_state["settings_vector_delete_error"] = result.get(
            "error",
            result.get("message", "Vector database deletion failed."),
        )
        return
    st.session_state["settings_confirm_delete_vector"] = False
    st.session_state["settings_vector_delete_error"] = None
    st.session_state["settings_vector_delete_success"] = result.get(
        "message",
        "Indexed vectors were deleted.",
    )
    st.session_state["health_status"] = None
    st.rerun()


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

    current_user = st.session_state.get("current_user") or {}
    user_id = current_user.get("user_id")
    if st.session_state.get("settings_profile_owner") != user_id:
        st.session_state["settings_profile_owner"] = user_id
        st.session_state["settings_profile_name"] = current_user.get("name", "")
        st.session_state["settings_account_error"] = None
        st.session_state["settings_account_success"] = None
        st.session_state["settings_delete_error"] = None
    if st.session_state.get("settings_sessions_owner") != user_id:
        st.session_state["settings_sessions_owner"] = user_id
        st.session_state["settings_sessions_success"] = None
        _refresh_active_sessions()
    if st.session_state.get("settings_activity_owner") != user_id:
        st.session_state["settings_activity_owner"] = user_id
        st.session_state["settings_security_activity"] = None
        _refresh_security_activity()

    st.subheader("Profile")
    st.caption("Review your account details and manage your verified email.")
    account_error = st.session_state.get("settings_account_error")
    if account_error:
        st.error(account_error, title="Profile")
    account_success = st.session_state.get("settings_account_success")
    if account_success:
        st.success(account_success, title="Profile")
    st.write(f"**Display name:** {current_user.get('name') or 'Not set'}")
    st.write(f"**Email:** {current_user.get('email', 'Not available')}")
    st.write(f"**Account created:** {current_user.get('created_at', 'Not available')}")
    st.write(f"**Role:** {current_user.get('role', 'user')}")

    with st.form("settings_profile_form"):
        st.text_input(
            "Display name",
            key="settings_profile_name",
            max_chars=120,
        )
        st.form_submit_button(
            "Save profile",
            key="settings_profile_submit",
            on_click=_submit_profile_update,
        )

    with st.form("settings_email_change_form"):
        st.text_input(
            "New email address",
            key="settings_new_email",
            max_chars=254,
        )
        st.text_input(
            "Current password",
            type="password",
            key="settings_email_current_password",
            max_chars=72,
        )
        st.caption(
            "Your current email stays active until you verify the new address."
        )
        st.form_submit_button(
            "Send verification email",
            key="settings_email_change_submit",
            on_click=_submit_email_change,
        )

    st.subheader("Account security")
    st.caption(
        "Changing your password revokes other sessions. This device stays "
        "signed in with a renewed session."
    )
    security_error = st.session_state.get("settings_security_error")
    if security_error:
        st.error(security_error, title="Account security")
    security_success = st.session_state.get("settings_security_success")
    if security_success:
        st.success(security_success, title="Account security")

    with st.form("settings_change_password_form"):
        st.text_input(
            "Current password",
            type="password",
            key="settings_current_password",
            max_chars=72,
        )
        st.text_input(
            "New password",
            type="password",
            key="settings_new_password",
            max_chars=72,
        )
        st.text_input(
            "Confirm new password",
            type="password",
            key="settings_confirm_password",
            max_chars=72,
        )
        st.caption(
            "Use at least 12 characters with lowercase, uppercase, a number, "
            "and a special character."
        )
        st.form_submit_button(
            "Change password",
            key="settings_change_password_submit",
            type="primary",
            on_click=_submit_password_change,
        )

    if st.session_state.get("confirm_logout_all_devices", False):
        st.warning(
            "This signs out every device, including this current session. "
            "You will need to sign in again."
        )
        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            st.button(
                "Confirm log out all devices",
                key="settings_confirm_logout_all",
                type="primary",
                on_click=_confirm_logout_all,
            )
        with cancel_col:
            st.button(
                "Cancel",
                key="settings_cancel_logout_all",
                on_click=_cancel_logout_all,
            )
    else:
        st.button(
            "Log out all devices",
            key="settings_logout_all_devices",
            on_click=_request_logout_all_confirmation,
        )

    st.subheader("Active sessions")
    st.caption(
        "Review devices signed in to your account. Revoking this device "
        "immediately signs you out."
    )
    st.button(
        "Refresh sessions",
        key="settings_refresh_sessions",
        on_click=_refresh_active_sessions,
    )
    sessions_error = st.session_state.get("settings_sessions_error")
    if sessions_error:
        st.error(sessions_error, title="Active sessions")
    sessions_success = st.session_state.get("settings_sessions_success")
    if sessions_success:
        st.success(sessions_success, title="Active sessions")
    active_sessions = st.session_state.get("settings_active_sessions")
    if active_sessions is None:
        st.info("Active sessions could not be loaded. Try refreshing.")
    elif not active_sessions:
        st.info("No active sessions were found.")
    else:
        for session in active_sessions:
            is_current = session.get("is_current", False)
            with st.container(border=True):
                st.markdown(
                    "**This device (current session)**"
                    if is_current
                    else "**Other active device**"
                )
                st.text(f"Signed in: {session.get('login_at', 'Unknown')}")
                st.text(
                    "Last active: "
                    f"{session.get('last_activity_at', 'Unknown')}"
                )
                st.text(f"Expires: {session.get('expires_at', 'Unknown')}")
                st.text(
                    "Device: "
                    f"{session.get('user_agent_family') or 'Unknown'}"
                )
                st.text(f"Masked IP: {session.get('ip_masked') or 'Unknown'}")
                st.button(
                    "Sign out this device"
                    if is_current
                    else "Revoke session",
                    key=f"settings_revoke_session_{session['session_id']}",
                    on_click=_revoke_active_session,
                    args=(session["session_id"],),
                )

    st.subheader("Recent security activity")
    st.caption(
        "Recent sign-ins and account-security changes for your account. "
        "Network addresses, when shown, are masked."
    )
    st.button(
        "Refresh activity",
        key="settings_refresh_security_activity",
        on_click=_refresh_security_activity,
    )
    activity_error = st.session_state.get(
        "settings_security_activity_error"
    )
    if activity_error:
        st.error(activity_error, title="Security activity")
    activity_events = st.session_state.get("settings_security_activity")
    if activity_events is None:
        st.info("Security activity could not be loaded. Try refreshing.")
    elif not activity_events:
        st.info("No recent security activity was found.")
    else:
        for event in activity_events:
            metadata = event.get("metadata") or {}
            details = []
            if metadata.get("source"):
                details.append(f"Source: {metadata['source']}")
            if metadata.get("user_agent_family"):
                details.append(f"Device: {metadata['user_agent_family']}")
            if metadata.get("ip_masked"):
                details.append(f"Masked IP: {metadata['ip_masked']}")
            if metadata.get("reason_code"):
                details.append(f"Reason: {metadata['reason_code']}")
            if metadata.get("revoked_session_count") is not None:
                details.append(
                    f"Sessions revoked: {metadata['revoked_session_count']}"
                )
            with st.container(border=True):
                st.markdown(
                    f"**{_SECURITY_ACTIVITY_LABELS.get(event.get('event_type'), 'Account activity')}**"
                )
                st.caption(event.get("occurred_at", "Timestamp unavailable"))
                if details:
                    st.caption(" · ".join(details))
    
    # AI Model Configuration
    st.markdown("""
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">AI Model Configuration</h3>
            <p class="card-subtitle">Language model and retrieval settings</p>
        </div>
    """, unsafe_allow_html=True)
    
    health = get_health_status()
    health_data = health or {}
    groq_status = get_groq_key_status(health_data)
    
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
            "Maximum Distance Threshold",
            0.0, 1.0, settings.SIMILARITY_THRESHOLD, 0.05,
            help="Maximum Chroma distance; lower distances indicate closer matches.",
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
        st.code(API_BASE_URL, language="text")
        st.caption("Base URL for all API requests")
    
    with col2:
        st.markdown("**Groq API key**")
        st.markdown(f"""
        <div style="padding: 12px; background: var(--color-bg-secondary); border-radius: var(--radius-md); margin-bottom: 16px;">
            <div style="font-weight: 500;">{groq_status['label']}</div>
            <div style="font-size: 0.875rem; color: var(--color-text-muted);">{groq_status['details']}</div>
        </div>
        """, unsafe_allow_html=True)
    
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
        vector_status = get_vector_db_status(health_data)
        indexed_chunks = vector_status["indexed_chunks"]
        st.caption(f"Availability: {vector_status['label']}")
        st.caption(
            "Indexed chunks: "
            + (str(indexed_chunks) if indexed_chunks is not None else "Unknown")
        )
        st.caption(f"Location: {settings.CHROMA_PERSIST_DIR}")
        st.caption(f"Collection: {settings.CHROMA_COLLECTION_NAME}")
        if st.button("📊 View Collection Stats", use_container_width=True, key="settings_view_stats"):
            result = call_api("/health")
            if "error" in result:
                st.error(result["error"], title="Health check failed")
            else:
                vector_status = get_vector_db_status(result)
                st.json({
                    "database_status": vector_status["label"],
                    "indexed_chunks": (
                        vector_status["indexed_chunks"]
                        if vector_status["indexed_chunks"] is not None
                        else "Unknown"
                    ),
                    "embedding_model": result.get("embedding_model", "unknown"),
                    "llm_model": result.get("llm_model", "unknown"),
                })
        if st.button("📚 Manage Indexed Documents", use_container_width=True, key="settings_manage_documents"):
            st.session_state.current_page = "knowledge_base"
            st.rerun()
    
    with col2:
        st.markdown("**Document Storage**")
        st.caption(f"Location: {settings.DATA_DIR}")
        if st.button("🗂️ Open Data Folder", use_container_width=True, key="settings_open_data"):
            import os
            os.startfile(settings.DATA_DIR) if os.name == 'nt' else st.info(f"Open {settings.DATA_DIR} in file explorer")
    
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
        if st.button("🗑️ Clear My Chat Sessions", use_container_width=True, type="secondary", key="settings_clear_sessions"):
            from frontend.components import clear_all_sessions, clear_session_history, new_session
            result = clear_all_sessions()
            if "error" not in result:
                clear_session_history()
                new_session()
                st.success("Your chat sessions were cleared")
                st.rerun()
            else:
                st.error(f"Failed: {result['error']}")
    
    with col2:
        if is_current_user_admin():
            if st.session_state.get("settings_vector_delete_success"):
                st.success(
                    st.session_state.pop("settings_vector_delete_success")
                )
            if st.session_state.get("settings_vector_delete_error"):
                st.error(
                    st.session_state["settings_vector_delete_error"],
                    title="Vector database",
                )
            if st.session_state.get("settings_confirm_delete_vector"):
                st.warning(
                    "This permanently removes all indexed vectors. Source PDFs "
                    "and SQLite records will not be deleted."
                )
                confirm_col, cancel_col = st.columns(2)
                with confirm_col:
                    st.button(
                        "Confirm delete all indexed vectors",
                        key="settings_confirm_delete_vectordb",
                        type="primary",
                        on_click=_confirm_vector_database_deletion,
                    )
                with cancel_col:
                    st.button(
                        "Cancel",
                        key="settings_cancel_delete_vectordb",
                        on_click=_cancel_vector_database_deletion,
                    )
            else:
                st.button(
                    "🗑️ Delete Vector Database",
                    use_container_width=True,
                    type="secondary",
                    key="settings_delete_vectordb",
                    on_click=_request_vector_database_deletion,
                )
    
    st.subheader("Delete account")
    st.warning(
        "This permanently deletes your account, owned chat history, and "
        "associated feedback. Shared knowledge-base documents are not affected."
    )
    delete_error = st.session_state.get("settings_delete_error")
    if delete_error:
        st.error(delete_error, title="Account deletion")
    with st.form("settings_delete_account_form"):
        st.text_input(
            "Current password",
            type="password",
            key="settings_delete_current_password",
            max_chars=72,
        )
        st.text_input(
            "Type DELETE MY ACCOUNT to confirm",
            key="settings_delete_confirmation",
            max_chars=32,
        )
        st.form_submit_button(
            "Delete account",
            key="settings_delete_account_submit",
            type="primary",
            on_click=_submit_account_deletion,
        )
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    render_settings_page()