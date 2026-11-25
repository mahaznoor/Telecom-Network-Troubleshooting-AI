"""API client for communicating with the FastAPI backend."""

import os
import requests
from typing import Dict, Any, List, Optional


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")


def call_api(
    endpoint: str,
    method: str = "GET",
    data: Dict = None,
    timeout: int = 60
) -> Dict[str, Any]:
    """Call the backend API with error handling."""
    url = f"{API_BASE_URL}{endpoint}"
    try:
        if method == "GET":
            response = requests.get(url, params=data, timeout=timeout)
        elif method == "POST":
            response = requests.post(url, json=data, timeout=timeout)
        elif method == "DELETE":
            response = requests.delete(url, json=data, timeout=timeout)
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


def health_check() -> Dict[str, Any]:
    """Check system health."""
    return call_api("/health")


def ingest_documents(
    file_paths: List[str] = None,
    directory: str = None
) -> Dict[str, Any]:
    """Ingest PDF documents."""
    return call_api("/ingest", "POST", {
        "file_paths": file_paths,
        "directory": directory
    })


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


def clear_session(session_id: str) -> Dict[str, Any]:
    """Clear a specific chat session."""
    return call_api(f"/chat/{session_id}", "DELETE")


def clear_all_sessions() -> Dict[str, Any]:
    """Clear all chat sessions."""
    return call_api("/chat", "DELETE")


def get_collection_stats() -> Dict[str, Any]:
    """Get vector store statistics."""
    # This uses the chat service stats via health endpoint
    health = call_api("/health")
    if "error" not in health:
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


def delete_vector_database(confirm: bool = False) -> Dict[str, Any]:
    """Delete the vector database collection.

    Requires confirm=True to prevent accidental deletion.
    """
    return call_api("/vectordb", "DELETE", data={"confirm": confirm})