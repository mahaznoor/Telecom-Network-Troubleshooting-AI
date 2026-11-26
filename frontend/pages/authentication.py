"""Login and account registration forms."""

import streamlit as st

from frontend.components.api_client import (
    login_user,
    register_user,
    request_password_reset,
    verify_email_change,
    resend_verification,
    verify_email,
)
from frontend.components.session_state import (
    clear_authenticated_session,
    set_authenticated_session,
)


def _consume_verification_link() -> None:
    if "verify_email_change" in st.query_params:
        token = st.query_params.get("verify_email_change")
        del st.query_params["verify_email_change"]
        if not isinstance(token, str) or not token:
            result = {
                "error": (
                    "This email change link is invalid, expired, or already "
                    "used."
                )
            }
        else:
            result = verify_email_change(token)
        if "error" in result:
            st.session_state.auth_error = result["error"]
            st.session_state.auth_success = None
            st.session_state.auth_success_title = None
        else:
            clear_authenticated_session()
            st.session_state.auth_error = None
            st.session_state.auth_success = result.get(
                "message",
                "Email updated. Sign in again with your new address.",
            )
            st.session_state.auth_success_title = "Email updated"
        return

    if "verify_email" not in st.query_params:
        return
    token = st.query_params.get("verify_email")
    del st.query_params["verify_email"]
    if not isinstance(token, str) or not token:
        st.session_state.auth_error = (
            "This verification link is invalid, expired, or already used. "
            "Request a new one."
        )
        st.session_state.auth_success = None
        st.session_state.auth_success_title = None
        return

    result = verify_email(token)
    if "error" in result:
        st.session_state.auth_error = result["error"]
        st.session_state.auth_success = None
        st.session_state.auth_success_title = None
    else:
        st.session_state.auth_error = None
        st.session_state.auth_success = result.get(
            "message",
            "Email verified. You can now sign in.",
        )
        st.session_state.auth_success_title = "Email verified"


def _submit_login() -> None:
    email = st.session_state.get("auth_login_email", "")
    password = st.session_state.get("auth_login_password", "")
    result = login_user(email, password)
    st.session_state.auth_login_password = ""
    if "error" in result:
        st.session_state.auth_error = result["error"]
        st.session_state.auth_success = None
        st.session_state.auth_success_title = None
        return
    set_authenticated_session(result)


def _submit_registration() -> None:
    name = st.session_state.get("auth_register_name", "")
    email = st.session_state.get("auth_register_email", "")
    password = st.session_state.get("auth_register_password", "")
    result = register_user(name, email, password)
    st.session_state.auth_register_password = ""
    st.session_state.auth_resend_email = email
    if "error" in result:
        st.session_state.auth_error = result["error"]
        st.session_state.auth_success = None
        st.session_state.auth_success_title = None
        return
    st.session_state.auth_error = None
    st.session_state.auth_success_title = "Check your email"
    st.session_state.auth_success = result.get(
        "message",
        "Check your email for verification instructions.",
    )


def _submit_resend_verification() -> None:
    email = st.session_state.get("auth_resend_email", "")
    result = resend_verification(email)
    if "error" in result:
        st.session_state.auth_error = result["error"]
        st.session_state.auth_success = None
        st.session_state.auth_success_title = None
        return
    st.session_state.auth_error = None
    st.session_state.auth_success_title = "Check your email"
    st.session_state.auth_success = result.get(
        "message",
        "If this address is eligible, verification instructions will be sent.",
    )


def _show_password_reset_request() -> None:
    st.session_state["show_password_reset_request"] = True
    st.session_state.auth_error = None
    st.session_state.auth_success = None
    st.session_state.auth_success_title = None


def _show_sign_in() -> None:
    st.session_state["show_password_reset_request"] = False
    st.session_state.auth_error = None
    st.session_state.auth_success = None
    st.session_state.auth_success_title = None


def _submit_password_reset_request() -> None:
    email = st.session_state.get("auth_password_reset_email", "")
    result = request_password_reset(email)
    if "error" in result:
        st.session_state.auth_error = result["error"]
        st.session_state.auth_success = None
        st.session_state.auth_success_title = None
        return
    st.session_state.auth_error = None
    st.session_state.auth_success_title = "Check your email"
    st.session_state.auth_success = result.get(
        "message",
        "If an account exists for this address, password reset instructions "
        "will be sent.",
    )


def _render_password_reset_request() -> None:
    st.subheader("Forgot password?")
    st.caption(
        "Enter the email address for your account. If it is eligible, "
        "we will send password reset instructions."
    )
    with st.form("auth_password_reset_request_form"):
        st.text_input(
            "Email",
            key="auth_password_reset_email",
            max_chars=254,
        )
        st.form_submit_button(
            "Send reset link",
            key="auth_password_reset_request_submit",
            type="primary",
            on_click=_submit_password_reset_request,
        )
    if st.button("Back to sign in", key="auth_password_reset_back"):
        _show_sign_in()
        st.rerun()


def render_authentication_page() -> None:
    """Render login and registration before exposing authenticated pages."""
    _consume_verification_link()
    st.title("Telecom Intelligence AI")
    st.subheader("Sign in or create an account")
    st.caption(
        "New accounts must verify their email before signing in."
    )

    notice = st.session_state.get("auth_notice")
    if notice:
        st.warning(notice, title="Sign-in required")
    error = st.session_state.get("auth_error")
    if error:
        st.error(error, title="Authentication failed")
    success = st.session_state.get("auth_success")
    if success:
        st.success(
            success,
            title=st.session_state.get("auth_success_title", "Status"),
        )

    if st.session_state.get("show_password_reset_request", False):
        _render_password_reset_request()
        return

    login_tab, register_tab = st.tabs(["Sign in", "Register"])
    with login_tab:
        with st.form("auth_login_form"):
            st.text_input("Email", key="auth_login_email", max_chars=254)
            st.text_input(
                "Password",
                type="password",
                key="auth_login_password",
                max_chars=72,
            )
            st.form_submit_button(
                "Sign in",
                key="auth_login_submit",
                type="primary",
                on_click=_submit_login,
            )
        st.button(
            "Forgot password?",
            key="auth_forgot_password",
            on_click=_show_password_reset_request,
        )

    with register_tab:
        with st.form("auth_register_form"):
            st.text_input("Name", key="auth_register_name", max_chars=120)
            st.text_input("Email", key="auth_register_email", max_chars=254)
            st.text_input(
                "Password (at least 12 characters)",
                type="password",
                key="auth_register_password",
                max_chars=72,
            )
            st.form_submit_button(
                "Create account",
                key="auth_register_submit",
                on_click=_submit_registration,
            )
        st.subheader("Resend verification")
        with st.form("auth_resend_verification_form"):
            st.text_input(
                "Email for verification",
                key="auth_resend_email",
                max_chars=254,
            )
            st.form_submit_button(
                "Resend verification email",
                key="auth_resend_verification_submit",
                on_click=_submit_resend_verification,
            )
