"""Focused tests for the System Health API response and Streamlit page."""

import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api.routes import health_check
from backend.config import settings
from frontend.pages.system_health import build_component_statuses
from streamlit.testing.v1 import AppTest


class HealthServiceStub:
    def __init__(self, ready: bool, stats: dict):
        self.ready = ready
        self.stats = stats

    def is_ready(self) -> bool:
        return self.ready

    def get_collection_stats(self) -> dict:
        return self.stats


class HealthEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_database_access_separately_from_index_contents(self) -> None:
        service = HealthServiceStub(
            ready=True,
            stats={"document_count": 0, "embedding_model": "local-model"},
        )

        with patch.object(settings, "GROQ_API_KEY", "configured-key"):
            response = await health_check(service)

        self.assertEqual(response.status, "healthy")
        self.assertTrue(response.groq_configured)
        self.assertTrue(response.groq_client_initialized)
        self.assertTrue(response.vector_db_available)
        self.assertFalse(response.vector_db_ready)
        self.assertEqual(response.document_count, 0)

    async def test_reports_vector_database_query_failure_as_degraded(self) -> None:
        service = HealthServiceStub(
            ready=True,
            stats={"error": "database unavailable"},
        )

        response = await health_check(service)

        self.assertEqual(response.status, "degraded")
        self.assertFalse(response.vector_db_available)
        self.assertFalse(response.vector_db_ready)
        self.assertEqual(response.document_count, 0)
        self.assertEqual(response.embedding_model, "unknown")


class SystemHealthPageTests(unittest.TestCase):
    def test_components_do_not_claim_unverified_services_are_online(self) -> None:
        components = build_component_statuses(
            {
                "version": "1.0.0",
                "groq_configured": True,
                "groq_client_initialized": True,
                "vector_db_available": True,
                "vector_db_ready": False,
                "document_count": 0,
                "embedding_model": "BAAI/bge-small-en-v1.5",
                "llm_model": "configured-model",
            },
            "2026-11-26 14:00:00",
        )
        by_name = {component["name"]: component for component in components}

        self.assertEqual(by_name["FastAPI Backend"]["status"], "online")
        self.assertEqual(by_name["Groq LLM"]["status"], "configured")
        self.assertEqual(by_name["HuggingFace Embeddings"]["status"], "configured")
        self.assertEqual(by_name["ChromaDB Vector Store"]["status"], "empty")
        self.assertEqual(by_name["Document Ingestion Pipeline"]["status"], "not_checked")

    def test_chroma_availability_is_unknown_without_verified_field(self) -> None:
        components = build_component_statuses(
            {
                "groq_configured": True,
                "groq_client_initialized": True,
                "vector_db_ready": True,
                "document_count": 4,
            },
            "2026-11-26 14:00:00",
        )
        by_name = {component["name"]: component for component in components}

        self.assertEqual(by_name["ChromaDB Vector Store"]["status"], "unknown")
        self.assertEqual(
            by_name["ChromaDB Vector Store"]["details"],
            "Database availability was not reported",
        )

    def test_page_displays_backend_unavailable_error(self) -> None:
        app_path = Path(__file__).resolve().parents[1] / "frontend" / "streamlit_app.py"
        app = AppTest.from_file(str(app_path), default_timeout=60)
        app.session_state["current_page"] = "system_health"
        app.session_state["access_token"] = "test-token"
        app.session_state["current_user"] = {
            "email": "user@example.com",
            "role": "user",
        }
        app.session_state["health_status"] = {
            "error": "Cannot connect to backend. Is the API server running?"
        }
        app.run()

        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertIn("Cannot connect to backend", app.error[0].value)


if __name__ == "__main__":
    unittest.main()
