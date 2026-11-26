"""Focused tests for RAG input mapping and conversation history."""

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from backend.rag.chain import RAGChain
from backend.rag.prompts import get_condense_prompt, get_qa_prompt
from backend.rag.retriever import TelecomRetriever
from backend.services.chat_service import ChatService
from backend.vectorstore.chroma_db import ChromaVectorStore


class ScoredVectorStore(ChromaVectorStore):
    def __init__(self) -> None:
        self.mmr_documents = [
            Document(page_content="close match"),
            Document(page_content="distant match"),
        ]
        self.scored_documents = [
            (Document(page_content="close match"), 0.2),
            (Document(page_content="distant match"), 0.9),
        ]

    def max_marginal_relevance_search(
        self,
        query: str,
        k: int,
        fetch_k: int,
    ) -> list[Document]:
        return self.mmr_documents

    def similarity_search_with_score(
        self,
        query: str,
        k: int,
    ) -> list[tuple[Document, float]]:
        return self.scored_documents


class RAGChainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.documents = [
            Document(
                page_content="Check the link status before proceeding.",
                metadata={
                    "source_file": "network-manual.pdf",
                    "source_path": "C:\\private\\manuals\\network-manual.pdf",
                    "page_number": 7,
                },
            )
        ]
        self.retriever_inputs: list[Any] = []
        self.model_prompts: list[str] = []

        def retrieve(query: Any) -> list[Document]:
            self.retriever_inputs.append(query)
            return self.documents

        def respond(prompt_value: Any) -> AIMessage:
            messages = prompt_value.to_messages()
            prompt = "\n".join(str(message.content) for message in messages)
            self.model_prompts.append(prompt)

            if "Standalone Question:" in prompt:
                return AIMessage(content="standalone retrieval question")
            return AIMessage(content="grounded answer")

        chain = object.__new__(RAGChain)
        chain.retriever = RunnableLambda(retrieve)
        chain.llm = RunnableLambda(respond)
        chain.qa_prompt = get_qa_prompt()
        chain.condense_prompt = get_condense_prompt()
        chain._build_chain()
        self.chain = chain

    def test_prompt_requires_supported_structured_troubleshooting_sections(self) -> None:
        messages = get_qa_prompt().format_messages(
            context="[Source: manual.pdf, Page: 2]\nCheck link status.",
            chat_history="",
            question="How do I troubleshoot link loss?",
        )
        prompt = messages[0].content
        headings = [
            "## Issue Summary",
            "## Possible Causes",
            "## Step-by-Step Troubleshooting",
            "## Recommended Solution",
            "## Verification Steps",
            "## Sources / References",
        ]

        self.assertEqual(
            [prompt.index(heading) for heading in headings],
            sorted(prompt.index(heading) for heading in headings),
        )
        self.assertIn("Never invent commands", prompt)
        self.assertIn("without forcing the six-section template", prompt)
        self.assertIn("[Source: filename, Page: N]", prompt)

    async def test_new_question_uses_string_retrieval_input_and_keeps_response_shape(self) -> None:
        result = await self.chain.ainvoke("Hi", "")

        self.assertEqual(self.retriever_inputs, ["Hi"])
        self.assertEqual(set(result), {"answer", "context"})
        self.assertEqual(result["answer"], "grounded answer")
        self.assertEqual(result["context"], self.documents)
        self.assertIn("[Source: network-manual.pdf, Page: 7]", self.model_prompts[-1])
        self.assertIn("Question: Hi", self.model_prompts[-1])
        self.assertNotIn("Standalone Question:", "\n".join(self.model_prompts))

    async def test_follow_up_is_condensed_for_retrieval_but_original_history_reaches_qa(self) -> None:
        history = "Human: The router link is down.\nAssistant: Check the physical link."
        question = "What should I check next?"

        result = await self.chain.ainvoke(question, history)

        self.assertEqual(self.retriever_inputs, ["standalone retrieval question"])
        self.assertEqual(result["context"], self.documents)
        self.assertIn(history, self.model_prompts[0])
        self.assertIn(question, self.model_prompts[0])
        self.assertIn(f"Chat History:\n{history}", self.model_prompts[-1])
        self.assertIn(f"Question: {question}", self.model_prompts[-1])
        self.assertIn("[Source: network-manual.pdf, Page: 7]", self.model_prompts[-1])

    async def test_chat_service_response_keeps_frontend_fields_and_citations(self) -> None:
        service = object.__new__(ChatService)
        service.rag_chain = self.chain
        service.retriever = SimpleNamespace(
            k=5,
            similarity_threshold=0.7,
            use_mmr=True,
        )
        service.sessions = {}
        service.session_store = SimpleNamespace(
            session_exists_for_another_user=lambda session_id, user_id: False,
            get_messages=lambda session_id, user_id: [],
            save_exchange=lambda session_id, user_id, question, answer, sources: 42,
        )
        service._retriever_lock = asyncio.Lock()

        session_id = "d1f6b6a8-d19f-4d63-8a22-f0f6ded4fb67"
        result = await service.process_question(
            "Hi",
            user_id="test-user",
            session_id=session_id,
        )

        self.assertEqual(
            set(result),
            {"answer", "sources", "session_id", "message_id"},
        )
        self.assertEqual(result["answer"], "grounded answer")
        self.assertEqual(result["session_id"], session_id)
        self.assertEqual(result["message_id"], 42)
        self.assertEqual(result["sources"][0]["source_file"], "network-manual.pdf")
        self.assertEqual(result["sources"][0]["page_number"], 7)
        self.assertNotIn("source_path", result["sources"][0])
        self.assertEqual(self.retriever_inputs, ["Hi"])

    async def test_chat_service_preserves_general_reply_when_retrieval_is_empty(self) -> None:
        class GeneralResponseChain:
            async def ainvoke(self, question: str, chat_history: str) -> dict:
                return {"answer": "Hello! What network issue can I help with?", "context": []}

        service = object.__new__(ChatService)
        service.rag_chain = GeneralResponseChain()
        service.retriever = SimpleNamespace(
            k=5,
            similarity_threshold=0.7,
            use_mmr=True,
        )
        service.sessions = {}
        service.session_store = SimpleNamespace(
            session_exists_for_another_user=lambda session_id, user_id: False,
            get_messages=lambda session_id, user_id: [],
            save_exchange=lambda session_id, user_id, question, answer, sources: None,
        )
        service._retriever_lock = asyncio.Lock()

        result = await service.process_question(
            "Hi",
            user_id="test-user",
            session_id="d1f6b6a8-d19f-4d63-8a22-f0f6ded4fb67",
        )

        self.assertEqual(result["answer"], "Hello! What network issue can I help with?")
        self.assertEqual(result["sources"], [])

    async def test_saved_chat_response_hides_legacy_source_paths(self) -> None:
        service = object.__new__(ChatService)
        service.session_store = SimpleNamespace(
            get_session=lambda session_id, user_id: {
                "session_id": session_id,
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Answer",
                        "sources": [
                            {
                                "source_file": "manual.pdf",
                                "source_path": "C:\\private\\manual.pdf",
                                "page_number": 2,
                                "content_preview": "Relevant excerpt",
                            }
                        ],
                    }
                ],
            }
        )

        result = service.get_chat_session(
            "d1f6b6a8-d19f-4d63-8a22-f0f6ded4fb67",
            "test-user",
        )

        self.assertEqual(
            result["messages"][0]["sources"],
            [{
                "source_file": "manual.pdf",
                "page_number": 2,
                "content_preview": "Relevant excerpt",
            }],
        )


class RetrieverThresholdTests(unittest.IsolatedAsyncioTestCase):
    async def test_mmr_returns_no_results_when_all_scores_exceed_threshold(self) -> None:
        vector_store = ScoredVectorStore()
        retriever = TelecomRetriever(
            vector_store=vector_store,
            k=2,
            similarity_threshold=0.1,
            use_mmr=True,
        )

        self.assertEqual(retriever.invoke("query"), [])

    async def test_search_endpoint_path_uses_threshold_and_restores_settings(self) -> None:
        retriever = TelecomRetriever(
            vector_store=ScoredVectorStore(),
            k=2,
            similarity_threshold=0.7,
            use_mmr=True,
        )
        service = object.__new__(ChatService)
        service.retriever = retriever
        service._retriever_lock = asyncio.Lock()

        result = await service.search_documents(
            "query",
            top_k=2,
            similarity_threshold=0.5,
            use_mmr=True,
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["content_preview"], "close match")
        self.assertEqual(retriever.k, 2)
        self.assertEqual(retriever.similarity_threshold, 0.7)
        self.assertTrue(retriever.use_mmr)


if __name__ == "__main__":
    unittest.main()
