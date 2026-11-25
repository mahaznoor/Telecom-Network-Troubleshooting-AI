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
from backend.services.chat_service import ChatService


class RAGChainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.documents = [
            Document(
                page_content="Check the link status before proceeding.",
                metadata={
                    "source_file": "network-manual.pdf",
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
        service._retriever_lock = asyncio.Lock()

        result = await service.process_question("Hi", session_id="test-session")

        self.assertEqual(set(result), {"answer", "sources", "session_id"})
        self.assertEqual(result["answer"], "grounded answer")
        self.assertEqual(result["session_id"], "test-session")
        self.assertEqual(result["sources"][0]["source_file"], "network-manual.pdf")
        self.assertEqual(result["sources"][0]["page_number"], 7)
        self.assertEqual(self.retriever_inputs, ["Hi"])


if __name__ == "__main__":
    unittest.main()
