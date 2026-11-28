"""Tests that answers only keep citations matching retrieved sources."""

import asyncio
import unittest
from types import SimpleNamespace

from langchain_core.documents import Document

from backend.rag.prompts import get_qa_prompt
from backend.services.chat_service import ChatService, strip_unsupported_citations

SOURCES = [
    {"source_file": "manual.pdf", "page_number": 2},
    {"source_file": "manual.pdf", "page_number": 4},
]


class StripUnsupportedCitationsTests(unittest.TestCase):
    def test_keeps_matching_and_removes_invented_page(self) -> None:
        answer = "Check the link [Source: manual.pdf, Page: 2]. Reboot [Source: manual.pdf, Page: 7]."
        result = strip_unsupported_citations(answer, SOURCES)
        self.assertIn("[Source: manual.pdf, Page: 2]", result)
        self.assertNotIn("Page: 7", result)
        self.assertEqual(result, "Check the link [Source: manual.pdf, Page: 2]. Reboot.")

    def test_removes_unknown_file(self) -> None:
        result = strip_unsupported_citations("Do X [Source: other.pdf, Page: 2]", SOURCES)
        self.assertNotIn("other.pdf", result)

    def test_file_match_is_case_insensitive(self) -> None:
        answer = "Do X [Source: MANUAL.pdf, Page: 4]"
        self.assertEqual(strip_unsupported_citations(answer, SOURCES), answer)

    def test_no_sources_removes_every_citation(self) -> None:
        result = strip_unsupported_citations("A [Source: manual.pdf, Page: 2] B", [])
        self.assertNotIn("[Source:", result)

    def test_drops_empty_bullet_left_in_references_list(self) -> None:
        answer = (
            "## Sources / References\n"
            "- [Source: manual.pdf, Page: 2]\n"
            "- [Source: manual.pdf, Page: 7]\n"
        )
        result = strip_unsupported_citations(answer, SOURCES)
        self.assertEqual(result, "## Sources / References\n- [Source: manual.pdf, Page: 2]\n")

    def test_answer_without_citations_is_unchanged(self) -> None:
        self.assertEqual(strip_unsupported_citations("Hello there.", SOURCES), "Hello there.")
        self.assertEqual(strip_unsupported_citations("", SOURCES), "")


class PromptInstructionTests(unittest.TestCase):
    def test_prompt_forbids_inventing_page_numbers(self) -> None:
        text = get_qa_prompt().messages[0].prompt.template
        self.assertIn("Never invent, guess, or extrapolate a page number", text)


class ProcessQuestionCitationTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_question_strips_invented_citation(self) -> None:
        class Chain:
            async def ainvoke(self, question, chat_history):
                return {
                    "answer": "Fix it [Source: manual.pdf, Page: 2] or [Source: manual.pdf, Page: 7].",
                    "context": [
                        Document(page_content="text", metadata={"source_file": "manual.pdf", "page_number": 2})
                    ],
                }

        service = object.__new__(ChatService)
        service.rag_chain = Chain()
        service.retriever = SimpleNamespace(k=5, similarity_threshold=0.7, use_mmr=True)
        service.sessions = {}
        service.session_store = SimpleNamespace(
            session_exists_for_another_user=lambda s, u: False,
            get_messages=lambda s, u: [],
        )
        service._retriever_lock = asyncio.Lock()
        saved = {}
        service._update_session = lambda sid, uid, q, a, src: saved.update(answer=a) or "m1"

        result = await service.process_question(
            "q", user_id="u", session_id="d1f6b6a8-d19f-4d63-8a22-f0f6ded4fb67"
        )
        self.assertIn("Page: 2", result["answer"])
        self.assertNotIn("Page: 7", result["answer"])
        self.assertNotIn("Page: 7", saved["answer"])


if __name__ == "__main__":
    unittest.main()
