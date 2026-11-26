"""Focused tests for readable structured and general chat responses."""

import unittest
from unittest.mock import patch

from frontend.pages.ai_assistant import (
    format_structured_answer,
    render_source_citations,
)
from frontend.pages.overview import render_recent_activity


class StructuredResponseFormattingTests(unittest.TestCase):
    def test_normalizes_section_headings_and_preserves_citations(self) -> None:
        answer = (
            "Problem Summary:\nThe link is down.\n\n"
            "Diagnostic Steps:\n1. Check link status.\n\n"
            "Source References:\n[Source: manual.pdf, Page: 2]"
        )

        formatted = format_structured_answer(answer)

        self.assertIn("## Issue Summary", formatted)
        self.assertIn("## Step-by-Step Troubleshooting", formatted)
        self.assertIn("## Sources / References", formatted)
        self.assertIn("[Source: manual.pdf, Page: 2]", formatted)

    def test_leaves_general_reply_unstructured(self) -> None:
        answer = "Hello! What network issue can I help with?"

        self.assertEqual(format_structured_answer(answer), answer)

    def test_source_citations_escape_uploaded_document_html(self) -> None:
        with patch("frontend.pages.ai_assistant.st.markdown") as render:
            render_source_citations([{
                "source_file": "<img src=x onerror=alert(1)>.pdf",
                "page_number": 1,
                "content_preview": "</div><script>alert(1)</script>",
            }])

        rendered = "\n".join(str(call.args[0]) for call in render.call_args_list)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;img", rendered)

    def test_overview_escapes_user_supplied_chat_html(self) -> None:
        with (
            patch(
                "frontend.components.session_state.get_chat_history",
                return_value=[{
                    "role": "user",
                    "content": "<img src=x onerror=alert(1)>",
                }],
            ),
            patch("frontend.pages.overview.st.markdown") as render,
        ):
            render_recent_activity()

        rendered = "\n".join(str(call.args[0]) for call in render.call_args_list)
        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;img", rendered)


if __name__ == "__main__":
    unittest.main()
