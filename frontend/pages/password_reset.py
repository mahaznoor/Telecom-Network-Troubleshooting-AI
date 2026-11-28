"""Password reset page for single-use email links."""

import streamlit as st

from frontend.components.api_client import reset_password
from frontend.components.session_state import clear_authenticated_session


def _submit_password_reset(token: str) -> None:
    new_password = st.session_state.get("password_reset_new_password", "")
    confirmation = st.session_state.get("password_reset_confirmation", "")
    st.session_state["password_reset_new_password"] = ""
    st.session_state["password_reset_confirmation"] = ""

    if new_password != confirmation:
        st.session_state["password_reset_error"] = (
            "The password and confirmation do not match."
        )
        return

    result = reset_password(token, new_password)
    if result.get("error"):
        st.session_state["password_reset_error"] = result["error"]
        return

    clear_authenticated_session()
    st.session_state["password_reset_error"] = None
    st.session_state["auth_success_title"] = "Password updated"
    st.session_state["auth_success"] = (
        "Your password has been updated. You can now sign in."
    )
    if "reset_password" in st.query_params:
        del st.query_params["reset_password"]


def render_password_reset_page() -> None:
    """Render the new-password form for a reset link."""
    token = st.query_params.get("reset_password")
    st.title("Reset your password")
    if not isinstance(token, str) or not token or len(token) > 256:
        st.error("This reset link is invalid, expired, or already used.")
        return

    st.caption(
        "Choose a password with at least 12 characters, including lowercase, "
        "uppercase, a number, and a special character."
    )
    with st.form("password_reset_form"):
        st.text_input(
            "New password",
            type="password",
            key="password_reset_new_password",
            max_chars=72,
        )
        st.text_input(
            "Confirm new password",
            type="password",
            key="password_reset_confirmation",
            max_chars=72,
        )
        st.form_submit_button(
            "Update password",
            key="password_reset_submit",
            type="primary",
            on_click=_submit_password_reset,
            args=(token,),
        )
    error = st.session_state.get("password_reset_error")
    if error:
        st.error(error)
