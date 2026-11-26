"""Tests for accurate service-status labels across Streamlit pages."""

import unittest
from pathlib import Path

from frontend.components.health_status import (
    get_groq_key_status,
    get_vector_db_status,
)
from streamlit.testing.v1 import AppTest


ENTRYPOINT = Path(__file__).resolve().parents[1] / "frontend" / "streamlit_app.py"


class SharedHealthStatusTests(unittest.TestCase):
    def test_groq_status_reports_configuration_not_connectivity(self) -> None:
        configured = get_groq_key_status({"groq_configured": True})
        unconfigured = get_groq_key_status({"groq_configured": False})

        self.assertEqual(configured["label"], "API key configured")
        self.assertEqual(configured["status"], "configured")
        self.assertIn("connectivity is not tested", configured["details"])
        self.assertEqual(unconfigured["label"], "Not configured")
        self.assertEqual(unconfigured["status"], "not_configured")

    def test_vector_database_availability_and_chunk_count_are_independent(self) -> None:
        available = get_vector_db_status(
            {"vector_db_available": True, "document_count": 5}
        )
        empty = get_vector_db_status(
            {"vector_db_available": True, "document_count": 0}
        )
        unavailable = get_vector_db_status(
            {"vector_db_available": False, "document_count": 0}
        )

        self.assertEqual((available["label"], available["indexed_chunks"]), ("Available", 5))
        self.assertEqual((empty["label"], empty["indexed_chunks"]), ("Available", 0))
        self.assertEqual(
            (unavailable["label"], unavailable["indexed_chunks"]),
            ("Unavailable", None),
        )

    def test_missing_availability_is_not_inferred_from_empty_state(self) -> None:
        status = get_vector_db_status({"vector_db_ready": False, "document_count": 0})

        self.assertEqual(status["status"], "unknown")
        self.assertEqual(status["indexed_chunks"], None)


class StatusIndicatorPageTests(unittest.TestCase):
    def make_health(self, groq_configured: bool, vector_db_available: bool, chunk_count: int) -> dict:
        return {
            "status": "healthy" if groq_configured and vector_db_available else "degraded",
            "version": "1.0.0",
            "groq_configured": groq_configured,
            "groq_client_initialized": groq_configured,
            "vector_db_available": vector_db_available,
            "vector_db_ready": vector_db_available and chunk_count > 0,
            "document_count": chunk_count,
            "embedding_model": "local-model",
            "llm_model": "configured-model",
        }

    @staticmethod
    def rendered_text(app: AppTest) -> str:
        values = []
        for element_type in ("markdown", "caption", "metric"):
            values.extend(
                str(element.value)
                for element in getattr(app, element_type, [])
                if getattr(element, "value", None) is not None
            )
        return "\n".join(values)

    def assert_page_statuses(self, page: str) -> None:
        cases = [
            (True, True, 5, "API key configured", "Available", "Indexed chunks: 5"),
            (False, True, 0, "Not configured", "Available", "Indexed chunks: 0"),
            (True, False, 0, "API key configured", "Unavailable", "Indexed chunks: Unknown"),
        ]
        app = AppTest.from_file(str(ENTRYPOINT), default_timeout=60)
        app.session_state["current_page"] = page
        app.session_state["access_token"] = "test-user-token"
        app.session_state["current_user"] = {
            "email": "user@example.com",
            "role": "user",
        }

        for groq_configured, db_available, chunk_count, groq_label, db_label, count_label in cases:
            app.session_state["health_status"] = self.make_health(
                groq_configured,
                db_available,
                chunk_count,
            )
            app.run()

            self.assertFalse(app.exception, f"{page} page raised: {app.exception}")
            rendered = self.rendered_text(app)
            self.assertIn(groq_label, rendered)
            self.assertNotIn("Connected", rendered)
            self.assertIn(f"Vector DB: {db_label}", rendered)
            self.assertIn(count_label, rendered)
            if not db_available:
                self.assertNotIn("Vector DB: Empty", rendered)

    def test_overview_status_wording_and_states(self) -> None:
        self.assert_page_statuses("overview")

    def test_settings_status_wording_and_states(self) -> None:
        self.assert_page_statuses("settings")

    def test_sidebar_status_wording_and_states(self) -> None:
        self.assert_page_statuses("overview")


if __name__ == "__main__":
    unittest.main()
