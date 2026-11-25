"""RAG chain for question answering using Groq LLM."""

from typing import Dict, Any, List
from langchain_groq import ChatGroq
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableBranch, RunnableLambda, RunnableParallel
from langchain_core.documents import Document
from backend.rag.prompts import get_qa_prompt, get_condense_prompt
from backend.config import settings


class RAGChain:
    """RAG chain for telecom network troubleshooting using Groq LLM."""

    def __init__(
        self,
        retriever,
        llm_model: str = None,
        temperature: float = None,
        max_tokens: int = None,
        groq_api_key: str = None
    ):
        self.retriever = retriever
        self.groq_api_key = groq_api_key or settings.GROQ_API_KEY
        self.llm_model = llm_model or settings.LLM_MODEL
        self.temperature = temperature or settings.LLM_TEMPERATURE
        self.max_tokens = max_tokens or settings.LLM_MAX_TOKENS

        if not self.groq_api_key:
            raise ValueError(
                "GROQ_API_KEY is required. Set it in .env file or environment variables."
            )

        self.llm = ChatGroq(
            model=self.llm_model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            groq_api_key=self.groq_api_key,
        )
        self.qa_prompt = get_qa_prompt()
        self.condense_prompt = get_condense_prompt()
        self._build_chain()

    def _build_chain(self):
        """Build the RAG chain with history awareness."""
        # Condense follow-up questions before retrieval when chat history exists.
        condense_chain = (
            self.condense_prompt
            | self.llm
            | StrOutputParser()
        )
        retrieval_question = RunnableBranch(
            (lambda inputs: not inputs.get("chat_history"), lambda inputs: inputs["question"]),
            condense_chain,
        )
        retrieval_chain = retrieval_question | self.retriever

        # Main QA chain
        qa_chain = (
            RunnableParallel({
                "context": RunnableLambda(lambda inputs: self._format_docs(inputs["context"])),
                "question": RunnableLambda(lambda inputs: inputs["question"]),
                "chat_history": RunnableLambda(lambda inputs: inputs.get("chat_history", "")),
            })
            | self.qa_prompt
            | self.llm
            | StrOutputParser()
        )

        # Combined chain with retrieval
        self.chain = RunnableParallel({
            "context": retrieval_chain,
            "question": RunnableLambda(lambda inputs: inputs["question"]),
            "chat_history": RunnableLambda(lambda inputs: inputs.get("chat_history", "")),
        }).assign(
            answer=qa_chain
        )

    def _format_docs(self, docs: List[Document]) -> str:
        """Format retrieved documents for the prompt with source and page citations."""
        if not docs:
            return "No relevant documents found."

        formatted = []
        for doc in docs:
            source_file = doc.metadata.get("source_file", "Unknown")
            page_number = doc.metadata.get("page_number", "Unknown")
            formatted.append(
                f"[Source: {source_file}, Page: {page_number}]\n{doc.page_content}"
            )
        return "\n\n---\n\n".join(formatted)

    def invoke(self, question: str, chat_history: str = "") -> Dict[str, Any]:
        """Invoke the chain with a question and optional chat history."""
        try:
            result = self.chain.invoke({
                "question": question,
                "chat_history": chat_history,
            })
            return {
                "answer": result["answer"],
                "context": result["context"],
            }
        except Exception as e:
            raise RuntimeError(f"RAG chain invocation failed: {e}") from e

    async def ainvoke(self, question: str, chat_history: str = "") -> Dict[str, Any]:
        """Async invoke the chain."""
        try:
            result = await self.chain.ainvoke({
                "question": question,
                "chat_history": chat_history,
            })
            return {
                "answer": result["answer"],
                "context": result["context"],
            }
        except Exception as e:
            raise RuntimeError(f"RAG chain async invocation failed: {e}") from e


def get_rag_chain(
    retriever,
    llm_model: str = None,
    temperature: float = None,
    max_tokens: int = None,
    groq_api_key: str = None
) -> RAGChain:
    """Factory function to create RAG chain with Groq LLM."""
    return RAGChain(
        retriever=retriever,
        llm_model=llm_model,
        temperature=temperature,
        max_tokens=max_tokens,
        groq_api_key=groq_api_key,
    )