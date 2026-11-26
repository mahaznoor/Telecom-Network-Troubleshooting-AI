"""Focused tests for persistent chat history and session navigation."""

import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from langchain_core.documents import Document
from streamlit.testing.v1 import AppTest

from backend.api.auth import AuthUser
from backend.api.routes import get_chat_session, list_chat_sessions
from backend.services.chat_service import ChatService
from backend.services.session_store import ChatSessionStore


ENTRYPOINT = Path(__file__).resolve().parents[1] / "frontend" / "streamlit_app.py"


class FakeRAGChain:
    def __init__(self) -> None:
        self.histories = []

    async def ainvoke(self, question: str, chat_history: str) -> dict:
        self.histories.append(chat_history)
        return {
            "answer": f"Answer to {question}",
            "context": [
                Document(
                    page_content="Check the link status.",
                    metadata={"source_file": "manual.pdf", "page_number": 3},
                )
            ],
        }


def build_service(session_store: ChatSessionStore, rag_chain: FakeRAGChain) -> ChatService:
    service = object.__new__(ChatService)
    service.session_store = session_store
    service.sessions = {}
    service.rag_chain = rag_chain
    service.retriever = SimpleNamespace(k=5, similarity_threshold=0.7, use_mmr=True)
    service._retriever_lock = asyncio.Lock()
    return service


class ChatSessionStoreTests(unittest.TestCase):
    def test_persists_titles_messages_sources_and_lists_after_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = str(Path(temp_dir) / "chat_history.sqlite3")
            session_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            sources = [{"source_file": "manual.pdf", "page_number": 3}]

            store = ChatSessionStore(database_path)
            store.create_user(user_id, "user@example.com", "bcrypt-hash", "user")
            store.save_exchange(
                session_id,
                user_id,
                "  How do I   check a link?  ",
                "Check the port status.",
                sources,
            )
            reopened_store = ChatSessionStore(database_path)

            session = reopened_store.get_session(session_id, user_id)
            summaries = reopened_store.list_sessions(user_id)

        self.assertEqual(session["title"], "How do I check a link?")
        self.assertEqual(session["messages"][0]["role"], "user")
        self.assertEqual(session["messages"][1]["sources"], sources)
        self.assertEqual(summaries[0]["session_id"], session_id)
        self.assertEqual(summaries[0]["message_count"], 2)

    def test_deleting_one_session_preserves_other_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChatSessionStore(str(Path(temp_dir) / "chat_history.sqlite3"))
            user_id = str(uuid.uuid4())
            store.create_user(user_id, "user@example.com", "bcrypt-hash", "user")
            first_id = str(uuid.uuid4())
            second_id = str(uuid.uuid4())
            store.save_exchange(first_id, user_id, "First question", "First answer", [])
            store.save_exchange(second_id, user_id, "Second question", "Second answer", [])

            self.assertTrue(store.delete_session(first_id, user_id))
            self.assertIsNone(store.get_session(first_id, user_id))
            self.assertIsNotNone(store.get_session(second_id, user_id))
            store.clear_all(user_id)
            self.assertEqual(store.list_sessions(user_id), [])


class ChatServiceSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_persists_and_never_crosses_session_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = str(Path(temp_dir) / "chat_history.sqlite3")
            session_a = str(uuid.uuid4())
            session_b = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            store = ChatSessionStore(database_path)
            store.create_user(user_id, "user@example.com", "bcrypt-hash", "user")

            first_chain = FakeRAGChain()
            first_service = build_service(store, first_chain)
            await first_service.process_question("Question for A", user_id, session_a)
            await first_service.process_question("Question for B", user_id, session_b)

            resumed_chain = FakeRAGChain()
            resumed_service = build_service(ChatSessionStore(database_path), resumed_chain)
            await resumed_service.process_question("Follow-up for A", user_id, session_a)
            await resumed_service.process_question(
                "First question for C", user_id, str(uuid.uuid4())
            )

        self.assertEqual(first_chain.histories, ["", ""])
        self.assertIn("Question for A", resumed_chain.histories[0])
        self.assertNotIn("Question for B", resumed_chain.histories[0])
        self.assertNotIn("Question for A", resumed_chain.histories[1])

    async def test_missing_and_invalid_ids_are_replaced_with_distinct_uuids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChatSessionStore(str(Path(temp_dir) / "chat_history.sqlite3"))
            user_id = str(uuid.uuid4())
            store.create_user(user_id, "user@example.com", "bcrypt-hash", "user")
            service = build_service(store, FakeRAGChain())

            invalid_result = await service.process_question(
                "First question", user_id, "not-a-uuid"
            )
            missing_result = await service.process_question("Second question", user_id)

        invalid_id = uuid.UUID(invalid_result["session_id"])
        missing_id = uuid.UUID(missing_result["session_id"])
        self.assertNotEqual(str(invalid_id), str(missing_id))
        self.assertIsNone(service.get_chat_session("not-a-uuid", user_id))


class ChatSessionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_routes_list_and_reject_invalid_or_missing_ids(self) -> None:
        session_id = str(uuid.uuid4())
        session = {
            "session_id": session_id,
            "title": "Check the link",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "messages": [],
        }

        class FakeChatService:
            def list_chat_sessions(self, user_id):
                return [{**session, "message_count": 0}]

            def get_chat_session(self, requested_id, user_id):
                return session if requested_id == session_id else None

        service = FakeChatService()
        current_user = AuthUser(
            user_id=str(uuid.uuid4()),
            email="user@example.com",
            role="user",
            created_at="2026-01-01T00:00:00+00:00",
        )
        summaries = await list_chat_sessions(
            current_user=current_user,
            chat_service=service,
        )
        loaded = await get_chat_session(
            session_id,
            current_user=current_user,
            chat_service=service,
        )

        self.assertEqual(summaries.sessions[0].title, "Check the link")
        self.assertEqual(loaded.session_id, session_id)
        with self.assertRaises(HTTPException) as error:
            await get_chat_session(
                "invalid",
                current_user=current_user,
                chat_service=service,
            )
        self.assertEqual(error.exception.status_code, 404)


class SidebarSessionTests(unittest.TestCase):
    def test_sidebar_opens_persisted_conversation_and_new_chat_is_unique(self) -> None:
        session_id = str(uuid.uuid4())
        conversation = {
            "session_id": session_id,
            "title": "Check the router link",
            "messages": [
                {
                    "message_id": 1,
                    "role": "user",
                    "content": "Check the router link",
                    "sources": [],
                },
                {
                    "message_id": 2,
                    "role": "assistant",
                    "content": "Check port status.",
                    "sources": [{"source_file": "manual.pdf", "page_number": 3}],
                },
            ],
        }
        app = AppTest.from_file(str(ENTRYPOINT), default_timeout=60)
        app.session_state["current_page"] = "ai_assistant"
        app.session_state["access_token"] = "test-token"
        app.session_state["current_user"] = {
            "email": "user@example.com",
            "role": "user",
        }

        with (
            patch(
                "frontend.components.sidebar.list_chat_sessions",
                return_value={"sessions": [{"session_id": session_id, "title": conversation["title"]}]},
            ),
            patch(
                "frontend.components.sidebar.get_chat_session",
                return_value=conversation,
            ),
        ):
            app.run()
            app.button(key=f"sidebar_chat_{session_id}").click().run()
            self.assertEqual(app.session_state["session_id"], session_id)
            self.assertEqual(app.session_state["messages"][1]["message_id"], 2)
            self.assertEqual(app.session_state["messages"][1]["sources"][0]["page_number"], 3)

        app = AppTest.from_file(str(ENTRYPOINT), default_timeout=60)
        app.session_state["current_page"] = "ai_assistant"
        app.session_state["access_token"] = "test-token"
        app.session_state["current_user"] = {
            "email": "user@example.com",
            "role": "user",
        }
        with patch(
            "frontend.components.sidebar.list_chat_sessions",
            return_value={"sessions": []},
        ):
            app.run()
            initial_id = app.session_state["session_id"]
            app.button(key="sidebar_new_chat").click().run()

        self.assertNotEqual(app.session_state["session_id"], initial_id)
        self.assertEqual(app.session_state["messages"], [])
        self.assertEqual(app.session_state["current_page"], "ai_assistant")


if __name__ == "__main__":
    unittest.main()
