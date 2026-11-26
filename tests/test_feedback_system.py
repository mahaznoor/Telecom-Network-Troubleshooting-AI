"""Focused tests for persistent response feedback and its Streamlit UI."""

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from backend.api.auth import AuthUser
from backend.api.routes import (
    FeedbackRequest,
    get_response_feedback_summary,
    submit_response_feedback as submit_feedback_route,
)
from backend.config import settings
from backend.services.chat_service import ChatService
from backend.services.session_store import (
    ChatSessionStore,
    FeedbackTargetNotFoundError,
)
from frontend.components.api_client import (
    get_feedback_summary,
    submit_response_feedback,
)


ENTRYPOINT = Path(__file__).resolve().parents[1] / "frontend" / "streamlit_app.py"


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "chat_history.sqlite3")
        self.store = ChatSessionStore(self.database_path)
        self.session_a = str(uuid.uuid4())
        self.session_b = str(uuid.uuid4())
        self.user_a = str(uuid.uuid4())
        self.user_b = str(uuid.uuid4())
        self.store.create_user(self.user_a, "a@example.com", "bcrypt-hash", "user")
        self.store.create_user(self.user_b, "b@example.com", "bcrypt-hash", "user")
        self.save_exchange(self.session_a, "Question A", "Answer A", [])
        self.save_exchange(self.session_b, "Question B", "Answer B", [])
        self.messages_a = self.get_messages(self.session_a)
        self.messages_b = self.get_messages(self.session_b)
        self.assistant_id_a = self.messages_a[1]["message_id"]
        self.assistant_id_b = self.messages_b[1]["message_id"]

    def owner_for(self, session_id: str) -> str:
        return self.user_a if session_id == self.session_a else self.user_b

    def save_exchange(self, session_id: str, question: str, answer: str, sources: list):
        return self.store.save_exchange(
            session_id,
            self.owner_for(session_id),
            question,
            answer,
            sources,
        )

    def get_messages(self, session_id: str):
        return self.store.get_messages(session_id, self.owner_for(session_id))

    def submit_feedback(
        self,
        session_id: str,
        message_id: int,
        rating: str,
        comment: str = None,
    ):
        return self.store.submit_feedback(
            session_id,
            self.owner_for(session_id),
            message_id,
            rating,
            comment,
        )

    def delete_session(self, session_id: str) -> bool:
        return self.store.delete_session(session_id, self.owner_for(session_id))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_submits_helpful_rating_with_optional_comment(self) -> None:
        feedback = self.submit_feedback(
            self.session_a,
            self.assistant_id_a,
            "helpful",
            "The cited checks helped.",
        )

        self.assertEqual(feedback["rating"], "helpful")
        self.assertEqual(feedback["comment"], "The cited checks helped.")
        self.assertEqual(feedback["session_id"], self.session_a)
        self.assertEqual(feedback["message_id"], self.assistant_id_a)

    def test_submits_not_helpful_rating_without_a_comment(self) -> None:
        feedback = self.submit_feedback(
            self.session_b,
            self.assistant_id_b,
            "not_helpful",
            "   ",
        )

        self.assertEqual(feedback["rating"], "not_helpful")
        self.assertIsNone(feedback["comment"])

    def test_update_changes_rating_and_comment_without_adding_a_second_record(self) -> None:
        first = self.submit_feedback(
            self.session_a, self.assistant_id_a, "helpful", "Initial comment"
        )
        updated = self.submit_feedback(
            self.session_a, self.assistant_id_a, "not_helpful", "Updated comment"
        )

        self.assertEqual(updated["feedback_id"], first["feedback_id"])
        self.assertEqual(updated["created_at"], first["created_at"])
        self.assertEqual(updated["rating"], "not_helpful")
        self.assertEqual(updated["comment"], "Updated comment")
        self.assertEqual(self.store.get_feedback_summary()["total_feedback"], 1)

    def test_rating_update_without_a_new_comment_preserves_existing_comment(self) -> None:
        self.submit_feedback(
            self.session_a, self.assistant_id_a, "helpful", "Keep this note."
        )

        updated = self.submit_feedback(
            self.session_a, self.assistant_id_a, "not_helpful"
        )

        self.assertEqual(updated["rating"], "not_helpful")
        self.assertEqual(updated["comment"], "Keep this note.")

    def test_rejects_unknown_mismatched_and_non_assistant_message_references(self) -> None:
        user_message_id = self.messages_a[0]["message_id"]
        with self.assertRaises(FeedbackTargetNotFoundError):
            self.store.submit_feedback(
                str(uuid.uuid4()), self.user_a, self.assistant_id_a, "helpful"
            )
        with self.assertRaises(FeedbackTargetNotFoundError):
            self.store.submit_feedback(
                self.session_b, self.user_b, self.assistant_id_a, "helpful"
            )
        with self.assertRaises(FeedbackTargetNotFoundError):
            self.store.submit_feedback(
                self.session_a, self.user_a, user_message_id, "helpful"
            )

    def test_feedback_and_message_ids_persist_after_reopening_database(self) -> None:
        feedback = self.submit_feedback(
            self.session_a, self.assistant_id_a, "helpful", "Persist this comment."
        )
        original_messages = self.get_messages(self.session_a)

        reopened_store = ChatSessionStore(self.database_path)
        reopened_feedback = reopened_store.get_feedback_summary()["recent_feedback"][0]
        reopened_messages = reopened_store.get_messages(self.session_a, self.user_a)

        self.assertEqual(reopened_feedback, feedback)
        self.assertEqual(reopened_messages, original_messages)
        self.assertEqual(reopened_messages[1]["message_id"], self.assistant_id_a)

    def test_summary_counts_percentage_filter_and_recent_order(self) -> None:
        third_message_id = self.save_exchange(
            self.session_a, "Another question", "Another answer", []
        )
        timestamps = [
            "2026-01-01T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
            "2026-01-03T00:00:00+00:00",
        ]
        with patch.object(ChatSessionStore, "_now", side_effect=timestamps):
            self.submit_feedback(
                self.session_a, self.assistant_id_a, "helpful"
            )
            self.submit_feedback(
                self.session_b, self.assistant_id_b, "not_helpful"
            )
            self.submit_feedback(self.session_a, third_message_id, "helpful")

        summary = self.store.get_feedback_summary(limit=10)
        filtered = self.store.get_feedback_summary(limit=10, rating="not_helpful")

        self.assertEqual(summary["total_feedback"], 3)
        self.assertEqual(summary["helpful_count"], 2)
        self.assertEqual(summary["not_helpful_count"], 1)
        self.assertEqual(summary["positive_percentage"], 66.67)
        self.assertEqual(
            [item["created_at"] for item in summary["recent_feedback"]],
            list(reversed(timestamps)),
        )
        self.assertEqual(filtered["total_feedback"], 3)
        self.assertEqual(filtered["not_helpful_count"], 1)
        self.assertEqual(len(filtered["recent_feedback"]), 1)
        self.assertEqual(filtered["recent_feedback"][0]["rating"], "not_helpful")

    def test_updating_feedback_moves_it_to_the_top_of_recent_activity(self) -> None:
        timestamps = [
            "2026-01-01T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
            "2026-01-03T00:00:00+00:00",
        ]
        with patch.object(ChatSessionStore, "_now", side_effect=timestamps):
            self.submit_feedback(
                self.session_a, self.assistant_id_a, "helpful"
            )
            self.submit_feedback(
                self.session_b, self.assistant_id_b, "helpful"
            )
            updated = self.submit_feedback(
                self.session_a, self.assistant_id_a, "not_helpful"
            )

        recent = self.store.get_feedback_summary()["recent_feedback"]

        self.assertEqual(recent[0]["feedback_id"], updated["feedback_id"])
        self.assertEqual(recent[0]["updated_at"], timestamps[2])

    def test_feedback_remains_associated_with_its_session(self) -> None:
        self.submit_feedback(
            self.session_a, self.assistant_id_a, "helpful", "For A"
        )
        self.submit_feedback(
            self.session_b, self.assistant_id_b, "not_helpful", "For B"
        )

        records = self.store.get_feedback_summary()["recent_feedback"]
        associations = {
            (item["session_id"], item["message_id"], item["comment"])
            for item in records
        }
        self.assertEqual(
            associations,
            {
                (self.session_a, self.assistant_id_a, "For A"),
                (self.session_b, self.assistant_id_b, "For B"),
            },
        )

    def test_store_rejects_invalid_ratings_and_comments_over_limit(self) -> None:
        with self.assertRaises(ValueError):
            self.submit_feedback(
                self.session_a, self.assistant_id_a, "neutral"
            )
        with self.assertRaises(ValueError):
            self.submit_feedback(
                self.session_a, self.assistant_id_a, "helpful", "x" * 1001
            )

    def test_chat_service_redacts_sensitive_comment_values_before_persistence(self) -> None:
        service = object.__new__(ChatService)
        service.session_store = self.store
        comment = (
            "api_key=local-demo-key token:local-token Bearer local-bearer "
            "gsk_demo-secret-123 configured-test-secret"
        )

        with patch.object(settings, "GROQ_API_KEY", "configured-test-secret"):
            feedback = service.submit_feedback(
                self.session_a,
                self.user_a,
                self.assistant_id_a,
                "helpful",
                comment,
            )

        self.assertNotIn("local-demo-key", feedback["comment"])
        self.assertNotIn("local-token", feedback["comment"])
        self.assertNotIn("local-bearer", feedback["comment"])
        self.assertNotIn("gsk_demo-secret-123", feedback["comment"])
        self.assertNotIn("configured-test-secret", feedback["comment"])

    def test_deleting_a_session_cascades_its_feedback_without_touching_other_sessions(self) -> None:
        self.submit_feedback(
            self.session_a, self.assistant_id_a, "helpful"
        )
        self.submit_feedback(
            self.session_b, self.assistant_id_b, "not_helpful"
        )

        self.assertTrue(self.delete_session(self.session_a))
        summary = self.store.get_feedback_summary()

        self.assertEqual(summary["total_feedback"], 1)
        self.assertEqual(summary["recent_feedback"][0]["session_id"], self.session_b)


class FeedbackRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_feedback_endpoints_submit_and_return_aggregate_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChatSessionStore(
                str(Path(temp_dir) / "chat_history.sqlite3")
            )
            session_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            store.create_user(user_id, "admin@example.com", "bcrypt-hash", "admin")
            store.save_exchange(session_id, user_id, "Question", "Answer", [])
            message_id = store.get_messages(session_id, user_id)[1]["message_id"]
            service = SimpleNamespace(
                submit_feedback=store.submit_feedback,
                get_feedback_summary=store.get_feedback_summary,
            )
            current_user = AuthUser(
                user_id=user_id,
                email="admin@example.com",
                role="admin",
                created_at="2026-01-01T00:00:00+00:00",
            )
            request = FeedbackRequest(
                session_id=session_id,
                message_id=message_id,
                rating="helpful",
                comment="Useful answer",
            )

            submitted = await submit_feedback_route(
                request,
                current_user=current_user,
                chat_service=service,
            )
            summary = await get_response_feedback_summary(
                20,
                None,
                current_user=current_user,
                chat_service=service,
            )

        self.assertEqual(submitted.message_id, message_id)
        self.assertEqual(submitted.comment, "Useful answer")
        self.assertEqual(summary.total_feedback, 1)
        self.assertEqual(summary.positive_percentage, 100.0)
        self.assertEqual(summary.recent_feedback[0].session_id, session_id)

    async def test_invalid_target_is_a_safe_not_found_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChatSessionStore(
                str(Path(temp_dir) / "chat_history.sqlite3")
            )
            current_user = AuthUser(
                user_id=str(uuid.uuid4()),
                email="user@example.com",
                role="user",
                created_at="2026-01-01T00:00:00+00:00",
            )
            service = SimpleNamespace(submit_feedback=store.submit_feedback)
            request = FeedbackRequest(
                session_id=str(uuid.uuid4()),
                message_id=10,
                rating="helpful",
            )

            with self.assertRaises(HTTPException) as error:
                await submit_feedback_route(
                    request,
                    current_user=current_user,
                    chat_service=service,
                )

        self.assertEqual(error.exception.status_code, 404)
        self.assertNotIn(temp_dir, error.exception.detail)
        self.assertNotIn("SQL", error.exception.detail)

    def test_request_validates_rating_and_comment_length(self) -> None:
        valid_fields = {
            "session_id": str(uuid.uuid4()),
            "message_id": 1,
        }
        with self.assertRaises(ValidationError):
            FeedbackRequest(**valid_fields, rating="neutral")
        with self.assertRaises(ValidationError):
            FeedbackRequest(
                **valid_fields,
                rating="helpful",
                comment="x" * 1001,
            )

    async def test_database_error_does_not_expose_internal_details(self) -> None:
        class FailingService:
            def submit_feedback(self, **kwargs):
                raise sqlite3.OperationalError("C:/private/chat.sqlite3 SQL detail")

        request = FeedbackRequest(
            session_id=str(uuid.uuid4()),
            message_id=1,
            rating="helpful",
        )
        current_user = AuthUser(
            user_id=str(uuid.uuid4()),
            email="user@example.com",
            role="user",
            created_at="2026-01-01T00:00:00+00:00",
        )
        with self.assertLogs("backend.api.routes", level="ERROR") as captured:
            with self.assertRaises(HTTPException) as error:
                await submit_feedback_route(
                    request,
                    current_user=current_user,
                    chat_service=FailingService(),
                )

        self.assertEqual(error.exception.status_code, 500)
        self.assertNotIn("private", error.exception.detail)
        self.assertNotIn("SQL", error.exception.detail)
        self.assertNotIn("private", "\n".join(captured.output))
        self.assertNotIn("SQL detail", "\n".join(captured.output))


class FeedbackClientTests(unittest.TestCase):
    def test_submit_sanitizes_validation_errors_and_preserves_safe_payload(self) -> None:
        with patch(
            "frontend.components.api_client.call_api",
            return_value={"error": 'HTTP 422: {"input":"sensitive comment"}'},
        ) as call_api:
            result = submit_response_feedback(
                "session-id", 8, "helpful", "My comment"
            )

        self.assertNotIn("sensitive comment", result["error"])
        self.assertEqual(call_api.call_args.args[:2], ("/feedback", "POST"))
        self.assertEqual(
            call_api.call_args.args[2],
            {
                "session_id": "session-id",
                "message_id": 8,
                "rating": "helpful",
                "comment": "My comment",
            },
        )

    def test_summary_client_passes_rating_filter(self) -> None:
        with patch(
            "frontend.components.api_client.call_api",
            return_value={"total_feedback": 0},
        ) as call_api:
            result = get_feedback_summary(limit=50, rating="not_helpful")

        self.assertEqual(result["total_feedback"], 0)
        self.assertEqual(call_api.call_args.args[:2], ("/feedback/summary",))
        self.assertEqual(
            call_api.call_args.kwargs["data"],
            {"limit": 50, "rating": "not_helpful"},
        )


class FeedbackDashboardTests(unittest.TestCase):
    def test_dashboard_shows_empty_state_when_no_feedback_exists(self) -> None:
        app = AppTest.from_file(str(ENTRYPOINT), default_timeout=60)
        app.session_state["current_page"] = "feedback_dashboard"
        app.session_state["access_token"] = "test-admin-token"
        app.session_state["current_user"] = {
        "email": "admin@example.com",
        "role": "admin",
        }
        with (
            patch(
                "frontend.components.sidebar.list_chat_sessions",
                return_value={"sessions": []},
            ),
            patch(
                "frontend.pages.feedback_dashboard.get_feedback_summary",
                return_value={
                    "total_feedback": 0,
                    "helpful_count": 0,
                    "not_helpful_count": 0,
                    "positive_percentage": 0.0,
                    "recent_feedback": [],
                },
            ),
        ):
            app.run()

        self.assertFalse(app.exception)
        self.assertIn(
            "No feedback has been submitted yet.",
            [element.value for element in app.info],
        )
        self.assertFalse(app.warning)

    def test_dashboard_filters_recent_feedback_and_shows_missing_comment_and_ids(self) -> None:
        feedback = {
            "feedback_id": 1,
            "session_id": "session-demo",
            "message_id": 12,
            "rating": "not_helpful",
            "comment": None,
            "created_at": "2026-11-26T10:00:00+00:00",
            "updated_at": "2026-11-26T10:00:00+00:00",
        }
        app = AppTest.from_file(str(ENTRYPOINT), default_timeout=60)
        app.session_state["current_page"] = "feedback_dashboard"
        app.session_state["access_token"] = "test-admin-token"
        app.session_state["current_user"] = {
            "email": "admin@example.com",
            "role": "admin",
        }
        with (
            patch(
                "frontend.components.sidebar.list_chat_sessions",
                return_value={"sessions": []},
            ),
            patch(
                "frontend.pages.feedback_dashboard.get_feedback_summary",
                return_value={
                    "total_feedback": 1,
                    "helpful_count": 0,
                    "not_helpful_count": 1,
                    "positive_percentage": 0.0,
                    "recent_feedback": [feedback],
                },
            ) as get_summary,
        ):
            app.run()
            app.segmented_control(
                key="feedback_dashboard_rating_filter"
            ).set_value("Not Helpful").run()

        get_summary.assert_called_with(limit=50, rating="not_helpful")
        self.assertFalse(app.exception)
        self.assertIn("No comment provided.", [item.value for item in app.text])
        self.assertTrue(
            any("Message ID: `12`" in item.value for item in app.caption)
        )
        self.assertTrue(
            any("2026-11-26T10:00:00+00:00" in item.value for item in app.caption)
        )

    def test_response_feedback_form_updates_rating_and_comment(self) -> None:
        session_id = str(uuid.uuid4())
        message_id = 17
        rating_key = f"feedback_rating_{session_id}_{message_id}"
        comment_key = f"feedback_comment_{session_id}_{message_id}"
        submit_key = f"feedback_submit_{session_id}_{message_id}"
        app = AppTest.from_string(
            "from frontend.pages.ai_assistant import render_response_feedback\n"
            f"render_response_feedback({session_id!r}, {message_id})"
        )
        with patch(
            "frontend.pages.ai_assistant.submit_response_feedback",
            return_value={"feedback_id": 3},
        ) as submit:
            app.run()
            app.get_by_key(rating_key).set_value(1)
            app.text_area(key=comment_key).set_value("Helpful and clear")
            app.form_submit_button(key=submit_key).click().run()

            app.get_by_key(rating_key).set_value(0)
            app.text_area(key=comment_key).set_value("")
            app.form_submit_button(key=submit_key).click().run()

        self.assertFalse(app.exception)
        self.assertEqual(submit.call_count, 2)
        self.assertEqual(
            submit.call_args_list[0].kwargs,
            {
                "session_id": session_id,
                "message_id": message_id,
                "rating": "helpful",
                "comment": "Helpful and clear",
            },
        )
        self.assertEqual(
            submit.call_args_list[1].kwargs,
            {
                "session_id": session_id,
                "message_id": message_id,
                "rating": "not_helpful",
                "comment": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
