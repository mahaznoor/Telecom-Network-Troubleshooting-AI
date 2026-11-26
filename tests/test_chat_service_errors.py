"""Tests for safe diagnostics when chat processing fails."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.config import settings
from backend.services.chat_service import ChatService


class FailingRAGChain:
    async def ainvoke(self, question: str, chat_history: str) -> dict:
        try:
            raise ValueError(
                "request failed with api_key=opaque-config-secret "
                "and token gsk_test_leaked_token_123"
            )
        except ValueError as error:
            raise RuntimeError(
                f"RAG chain async invocation failed: {error}"
            ) from error


class ChatServiceErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_processing_error_logs_traceback_without_secrets(self) -> None:
        service = object.__new__(ChatService)
        service.rag_chain = FailingRAGChain()
        service.retriever = SimpleNamespace(
            k=5,
            similarity_threshold=0.7,
            use_mmr=True,
        )
        service.sessions = {}
        service.session_store = SimpleNamespace(
            session_exists_for_another_user=lambda session_id, user_id: False,
            get_messages=lambda session_id, user_id: [],
        )
        service._retriever_lock = asyncio.Lock()

        with patch.object(settings, "GROQ_API_KEY", "opaque-config-secret"):
            with self.assertLogs("backend.services.chat_service", level="ERROR") as captured:
                result = await service.process_question(
                    "test question",
                    user_id="test-user",
                    session_id="d1f6b6a8-d19f-4d63-8a22-f0f6ded4fb67",
                )

        log_output = "\n".join(captured.output)
        self.assertEqual(result["error"], "processing_error")
        self.assertIn("Error processing question:", result["answer"])
        self.assertIn("Traceback (most recent call last)", log_output)
        self.assertIn("RuntimeError", log_output)
        self.assertIn("ValueError", log_output)
        self.assertNotIn("opaque-config-secret", log_output)
        self.assertNotIn("gsk_test_leaked_token_123", log_output)
        self.assertNotIn("opaque-config-secret", result["answer"])
        self.assertNotIn("gsk_test_leaked_token_123", result["answer"])
        self.assertIn("[REDACTED_SECRET]", result["answer"])
        self.assertIn("[REDACTED_GROQ_API_KEY]", result["answer"])


if __name__ == "__main__":
    unittest.main()
