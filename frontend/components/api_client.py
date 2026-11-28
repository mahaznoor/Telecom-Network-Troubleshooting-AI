"""API client for communicating with the FastAPI backend."""

import os
import requests
from typing import Dict, Any, List, Optional, Literal
from urllib.parse import quote

import streamlit as st

from backend.config import (
    MAX_DOCUMENT_UPLOAD_BYTES,
    MAX_DOCUMENT_UPLOAD_FILES,
    settings,
)

API_BASE_URL = os.getenv("API_BASE_URL", settings.API_BASE_URL).rstrip("/")
_PUBLIC_AUTH_ENDPOINTS = {"/auth/login", "/auth/register"}
_PUBLIC_AUTH_ENDPOINTS.update(
    {
        "/auth/verify-email",
        "/auth/verify-email-change",
        "/auth/resend-verification",
        "/auth/request-password-reset",
        "/auth/reset-password",
    }
)


def _request_headers(endpoint: str) -> Dict[str, str]:
    """Attach the in-memory bearer token without persisting it in browser storage."""
    headers: Dict[str, str] = {}
    token = st.session_state.get("access_token")
    if token and endpoint not in _PUBLIC_AUTH_ENDPOINTS:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _expire_authentication() -> None:
    from frontend.components.session_state import clear_authenticated_session

    clear_authenticated_session(
        "Your sign-in expired or is invalid. Please sign in again."
    )


def _safe_http_error(status_code: int, endpoint: str) -> str:
    """Map API failures to messages that do not include backend response bodies."""
    if status_code == 401 and endpoint in _PUBLIC_AUTH_ENDPOINTS:
        return "Invalid email or password." if endpoint == "/auth/login" else "Authentication request was rejected."
    if status_code == 401:
        _expire_authentication()
        return "Your sign-in expired or is invalid. Please sign in again."
    if status_code == 403:
        if endpoint == "/auth/login":
            return "Verify your email address before signing in."
        return "You do not have permission to perform this action."
    if status_code == 400 and endpoint == "/auth/verify-email":
        return (
            "This verification link is invalid, expired, or already used. "
            "Request a new one."
        )
    if status_code == 400 and endpoint == "/auth/reset-password":
        return "This reset link is invalid, expired, or already used."
    if status_code == 400 and endpoint == "/auth/change-password":
        return "Current password is incorrect."
    if status_code == 400 and endpoint in (
        "/auth/request-email-change",
        "/auth/account",
    ):
        return "Current password is incorrect."
    if status_code == 409 and endpoint == "/auth/request-email-change":
        return "That email address cannot be used."
    if status_code == 422 and endpoint == "/auth/profile":
        return "Enter a valid display name."
    if status_code == 422 and endpoint == "/auth/request-email-change":
        return "Enter a valid email address and current password."
    if status_code == 422 and endpoint == "/auth/account":
        return "Confirm deletion and enter a valid current password."
    if status_code == 400 and endpoint == "/auth/verify-email-change":
        return "This email change link is invalid, expired, or already used."
    if status_code == 422 and endpoint == "/auth/change-password":
        return (
            "The new password must differ from the current password and meet "
            "the password requirements."
        )
    if status_code == 404:
        return "The requested item was not found."
    if status_code == 409:
        return "Unable to register with these account details."
    if status_code == 422:
        return "The submitted information is invalid."
    if status_code == 503:
        return "The backend service is not configured for this operation."
    if status_code == 429:
        return "Please wait before trying this request again."
    if status_code == 413 and endpoint == "/ingest/upload":
        return "Uploads are limited to 200 MiB total and 100 PDFs per request."
    if status_code >= 500:
        return "The backend request failed. Please try again."
    return "The backend rejected the request."


def call_api(
    endpoint: str,
    method: str = "GET",
    data: Dict = None,
    timeout: int = 60,
    files: Optional[List[tuple[str, tuple[str, bytes, str]]]] = None,
) -> Dict[str, Any]:
    """Call the backend API with error handling."""
    url = f"{API_BASE_URL}{endpoint}"
    headers = _request_headers(endpoint)
    try:
        if files is not None and method != "POST":
            return {"error": "File uploads require a POST request."}
        if method == "GET":
            response = requests.get(url, params=data, headers=headers, timeout=timeout)
        elif method == "POST":
            if files is None:
                response = requests.post(
                    url,
                    json=data,
                    headers=headers,
                    timeout=timeout,
                )
            else:
                response = requests.post(
                    url,
                    files=files,
                    headers=headers,
                    timeout=timeout,
                )
        elif method == "PATCH":
            response = requests.patch(url, json=data, headers=headers, timeout=timeout)
        elif method == "DELETE":
            response = requests.delete(url, json=data, headers=headers, timeout=timeout)
        else:
            return {"error": "Invalid method"}

        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        return {"error": "Cannot connect to backend. Is the API server running?"}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out. The backend may be processing a large request."}
    except requests.exceptions.HTTPError as error:
        status_code = error.response.status_code if error.response is not None else 500
        return {"error": _safe_http_error(status_code, endpoint)}
    except requests.exceptions.RequestException:
        return {"error": "The backend request failed. Please try again."}


def register_user(name: str, email: str, password: str) -> Dict[str, Any]:
    """Register an unverified account and request a verification email."""
    result = call_api(
        "/auth/register",
        "POST",
        {"name": name, "email": email, "password": password},
        timeout=15,
    )
    if result.get("error"):
        return {"error": result["error"]}
    return result


def verify_email(token: str) -> Dict[str, Any]:
    """Verify an email address using its one-time link token."""
    return call_api(
        "/auth/verify-email",
        "POST",
        {"token": token},
        timeout=15,
    )


def resend_verification(email: str) -> Dict[str, Any]:
    """Request a verification email without revealing account existence."""
    return call_api(
        "/auth/resend-verification",
        "POST",
        {"email": email},
        timeout=15,
    )


def request_password_reset(email: str) -> Dict[str, Any]:
    """Request a reset email without disclosing whether the account exists."""
    return call_api(
        "/auth/request-password-reset",
        "POST",
        {"email": email},
        timeout=15,
    )


def reset_password(token: str, new_password: str) -> Dict[str, Any]:
    """Set a new password with a one-time reset token."""
    return call_api(
        "/auth/reset-password",
        "POST",
        {"token": token, "new_password": new_password},
        timeout=15,
    )


def change_password(
    current_password: str,
    new_password: str,
    revoke_other_sessions: bool = False,
) -> Dict[str, Any]:
    """Change the authenticated user's password."""
    return call_api(
        "/auth/change-password",
        "POST",
        {
            "current_password": current_password,
            "new_password": new_password,
            "revoke_other_sessions": revoke_other_sessions,
        },
        timeout=15,
    )


def logout_all_devices() -> Dict[str, Any]:
    """Revoke all access tokens for the authenticated account."""
    return call_api(
        "/auth/logout-all-devices",
        "POST",
        {},
        timeout=15,
    )


def list_active_sessions() -> Dict[str, Any]:
    """List the authenticated user's active login sessions."""
    return call_api("/auth/sessions", timeout=15)

def get_security_activity(limit: int = 20) -> Dict[str, Any]:
    """Load recent security events for the authenticated account."""
    return call_api("/auth/activity", data={"limit": limit}, timeout=15)


def revoke_active_session(session_id: str) -> Dict[str, Any]:
    """Revoke one active login session belonging to the current account."""
    return call_api(
        f"/auth/sessions/{quote(session_id, safe='')}",
        "DELETE",
        timeout=15,
    )


def update_profile(name: str) -> Dict[str, Any]:
    """Update the authenticated user's display name."""
    return call_api(
        "/auth/profile",
        "PATCH",
        {"name": name},
        timeout=15,
    )


def request_email_change(
    current_password: str,
    new_email: str,
) -> Dict[str, Any]:
    """Request verification before changing the account email."""
    return call_api(
        "/auth/request-email-change",
        "POST",
        {
            "current_password": current_password,
            "new_email": new_email,
        },
        timeout=15,
    )


def verify_email_change(token: str) -> Dict[str, Any]:
    """Verify a pending email change with its one-time link token."""
    return call_api(
        "/auth/verify-email-change",
        "POST",
        {"token": token},
        timeout=15,
    )


def delete_account(
    current_password: str,
    confirmation: str,
) -> Dict[str, Any]:
    """Delete the authenticated account after explicit confirmation."""
    return call_api(
        "/auth/account",
        "DELETE",
        {
            "current_password": current_password,
            "confirmation": confirmation,
        },
        timeout=15,
    )


def login_user(email: str, password: str) -> Dict[str, Any]:
    """Authenticate and retrieve a short-lived access token."""
    result = call_api(
        "/auth/login",
        "POST",
        {"email": email, "password": password},
        timeout=15,
    )
    if result.get("error"):
        return {"error": result["error"]}
    return result


def health_check() -> Dict[str, Any]:
    """Check system health."""
    return call_api("/health")


def ingest_documents(
    file_paths: List[str] = None,
    directory: str = None
) -> Dict[str, Any]:
    """Ingest PDF documents."""
    return call_api(
        "/ingest",
        "POST",
        {
            "file_paths": file_paths,
            "directory": directory,
        },
        timeout=300,
    )


def upload_documents(uploaded_files: List[Any]) -> Dict[str, Any]:
    """Upload PDFs as multipart data so the backend owns persistent storage."""
    if len(uploaded_files) > MAX_DOCUMENT_UPLOAD_FILES:
        return {
            "error": (
                f"Upload no more than {MAX_DOCUMENT_UPLOAD_FILES} PDFs at once."
            )
        }
    if sum(
        getattr(uploaded_file, "size", 0)
        for uploaded_file in uploaded_files
    ) > MAX_DOCUMENT_UPLOAD_BYTES:
        return {"error": "The upload batch must be 200 MiB or smaller."}

    files = [
        (
            "files",
            (
                uploaded_file.name,
                uploaded_file.getvalue(),
                "application/pdf",
            ),
        )
        for uploaded_file in uploaded_files
    ]
    return call_api(
        "/ingest/upload",
        "POST",
        files=files,
        timeout=300,
    )


def chat_question(
    question: str,
    session_id: str = None,
    top_k: int = 5,
    similarity_threshold: Optional[float] = None,
    use_mmr: Optional[bool] = None
) -> Dict[str, Any]:
    """Ask a question to the AI assistant."""
    payload: Dict[str, Any] = {
        "question": question,
        "session_id": session_id,
        "top_k": top_k,
    }
    if similarity_threshold is not None:
        payload["similarity_threshold"] = similarity_threshold
    if use_mmr is not None:
        payload["use_mmr"] = use_mmr
    return call_api("/chat", "POST", payload)


def submit_response_feedback(
    session_id: str,
    message_id: int,
    rating: Literal["helpful", "not_helpful"],
    comment: Optional[str] = None,
) -> Dict[str, Any]:
    """Create or update feedback for one persisted assistant response."""
    result = call_api(
        "/feedback",
        "POST",
        {
            "session_id": session_id,
            "message_id": message_id,
            "rating": rating,
            "comment": comment,
        },
        timeout=10,
    )
    error = result.get("error", "")
    if error.startswith("HTTP 422:"):
        return {
            "error": "Feedback was not accepted. Choose a rating and keep the comment within 1000 characters."
        }
    if error.startswith("HTTP 404:"):
        return {"error": "The conversation or response could not be found."}
    if error.startswith("HTTP "):
        return {"error": "Feedback could not be saved. Please try again."}
    return result


def get_feedback_summary(
    limit: int = 20,
    rating: Optional[Literal["helpful", "not_helpful"]] = None,
) -> Dict[str, Any]:
    """Get aggregate feedback counts and recent feedback records."""
    params: Dict[str, Any] = {"limit": limit}
    if rating is not None:
        params["rating"] = rating
    return call_api("/feedback/summary", data=params, timeout=10)


def list_chat_sessions() -> Dict[str, Any]:
    """List saved chat conversations."""
    return call_api("/chat/sessions", timeout=10)


def get_chat_session(session_id: str) -> Dict[str, Any]:
    """Load one saved conversation and its messages."""
    return call_api(f"/chat/sessions/{quote(session_id, safe='')}", timeout=10)


def clear_session(session_id: str) -> Dict[str, Any]:
    """Clear a specific chat session."""
    return call_api(f"/chat/{quote(session_id, safe='')}", "DELETE")


def clear_all_sessions() -> Dict[str, Any]:
    """Clear all chat sessions."""
    return call_api("/chat", "DELETE")


def get_collection_stats() -> Dict[str, Any]:
    """Get vector store statistics."""
    # This uses the chat service stats via health endpoint
    health = call_api("/health")
    if not health.get("error"):
        return {
            "document_count": health.get("document_count", 0),
            "embedding_model": health.get("embedding_model", "unknown"),
            "llm_model": health.get("llm_model", "unknown"),
            "vector_db_ready": health.get("vector_db_ready", False)
        }
    return health


def search_documents(
    query: str,
    top_k: int = 5,
    similarity_threshold: Optional[float] = None,
    use_mmr: Optional[bool] = None
) -> Dict[str, Any]:
    """Search indexed documents directly via ChromaDB (no Groq/LLM)."""
    params: Dict[str, Any] = {"q": query, "top_k": top_k}
    if similarity_threshold is not None:
        params["similarity_threshold"] = similarity_threshold
    if use_mmr is not None:
        params["use_mmr"] = use_mmr
    return call_api("/search", "GET", data=params)


def get_documents_list() -> Dict[str, Any]:
    """Get list of all unique indexed documents with metadata."""
    return call_api("/documents", "GET")

def delete_indexed_document(document_id: str) -> Dict[str, Any]:
    """Remove indexed chunks for one document as an administrator."""
    return call_api(
        f"/documents/{quote(document_id, safe='')}",
        "DELETE",
        timeout=30,
    )


def delete_vector_database(confirm: bool = False) -> Dict[str, Any]:
    """Delete the vector database collection.

    Requires confirm=True to prevent accidental deletion.
    """
    return call_api("/vectordb", "DELETE", data={"confirm": confirm})