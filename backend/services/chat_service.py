"""Chat service orchestrating the RAG pipeline with Groq LLM."""

import asyncio
import uuid
from typing import Dict, Any, List, Optional
from backend.ingestion.pdf_loader import PDFLoader
from backend.ingestion.text_splitter import TextSplitter
from backend.ingestion.embeddings import get_embeddings_model
from backend.vectorstore.chroma_db import ChromaVectorStore
from backend.rag.retriever import get_retriever
from backend.rag.chain import get_rag_chain, RAGChain
from backend.config import settings


class ChatService:
    """Main service orchestrating document ingestion and question answering."""

    def __init__(self):
        self.pdf_loader = PDFLoader(data_dir=settings.DATA_DIR)
        self.text_splitter = TextSplitter()
        self.vector_store = ChromaVectorStore()
        self.retriever = get_retriever(self.vector_store)
        self.rag_chain: Optional[RAGChain] = None
        self._initialize_rag_chain()
        self.sessions: Dict[str, List[Dict[str, str]]] = {}
        self._retriever_lock = asyncio.Lock()

    def _initialize_rag_chain(self):
        """Initialize the RAG chain with Groq LLM."""
        try:
            self.rag_chain = get_rag_chain(self.retriever)
        except ValueError as e:
            # API key not configured - chain will be None
            self.rag_chain = None
            print(f"Warning: RAG chain not initialized: {e}")

    def _ensure_rag_chain(self) -> RAGChain:
        """Ensure RAG chain is initialized, raise error if not."""
        if self.rag_chain is None:
            # Try to reinitialize in case API key was added
            self._initialize_rag_chain()
            if self.rag_chain is None:
                raise RuntimeError(
                    "RAG chain not available. GROQ_API_KEY is not configured. "
                    "Please set GROQ_API_KEY in .env file or environment variables."
                )
        return self.rag_chain

    async def process_question(
        self,
        question: str,
        session_id: Optional[str] = None,
        top_k: int = None,
        similarity_threshold: Optional[float] = None,
        use_mmr: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Process a troubleshooting question through the RAG pipeline."""
        # Validate input
        if not question or not question.strip():
            return {
                "answer": "Please provide a valid question.",
                "sources": [],
                "session_id": session_id or str(uuid.uuid4()),
                "error": "Empty question"
            }

        # Ensure RAG chain is available
        try:
            rag_chain = self._ensure_rag_chain()
        except RuntimeError as e:
            return {
                "answer": str(e),
                "sources": [],
                "session_id": session_id or str(uuid.uuid4()),
                "error": "configuration_error"
            }

        if session_id is None:
            session_id = str(uuid.uuid4())

        # Get chat history for session
        chat_history = self._get_chat_history(session_id)

        # Apply per-request retriever settings under lock to avoid race conditions
        # between concurrent requests sharing the same ChatService instance.
        async with self._retriever_lock:
            try:
                # Save current values while holding the lock, so no other
                # request can modify them between save and restore.
                old_k = self.retriever.k
                old_similarity_threshold = self.retriever.similarity_threshold
                old_use_mmr = self.retriever.use_mmr

                # Apply current request's settings
                if top_k:
                    self.retriever.k = top_k
                if similarity_threshold is not None:
                    self.retriever.similarity_threshold = similarity_threshold
                if use_mmr is not None:
                    self.retriever.use_mmr = use_mmr

                # Invoke RAG chain while holding the lock
                result = await rag_chain.ainvoke(question, chat_history)

                # Check if context is empty
                context_docs = result.get("context", [])
                if not context_docs:
                    answer = (
                        "I couldn't find relevant information in the uploaded manuals "
                        "to answer your question. Please ensure the relevant telecom "
                        "manuals have been ingested, or try rephrasing your question."
                    )
                    sources = []
                else:
                    answer = result["answer"]
                    sources = self._format_sources(context_docs)

                # Update session history
                self._update_session(session_id, question, answer)

                return {
                    "answer": answer,
                    "sources": sources,
                    "session_id": session_id,
                }
            except Exception as e:
                error_msg = str(e)
                # Handle specific error types
                if "rate limit" in error_msg.lower() or "429" in error_msg:
                    error_msg = "Groq API rate limit exceeded. Please try again later."
                elif "api key" in error_msg.lower() or "unauthorized" in error_msg.lower():
                    error_msg = "Invalid or missing Groq API key. Check GROQ_API_KEY configuration."
                elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                    error_msg = "Failed to connect to Groq API. Check network connectivity."

                return {
                    "answer": f"Error processing question: {error_msg}",
                    "sources": [],
                    "session_id": session_id,
                    "error": "processing_error"
                }
            finally:
                # Restore previous retriever settings even if invocation raises
                self.retriever.k = old_k
                self.retriever.similarity_threshold = old_similarity_threshold
                self.retriever.use_mmr = old_use_mmr

    def _format_sources(self, docs: List[Any]) -> List[Dict[str, Any]]:
        """Format source documents for response."""
        sources = []
        for doc in docs:
            metadata = doc.metadata if hasattr(doc, 'metadata') else {}
            sources.append({
                "source_file": metadata.get("source_file", "Unknown"),
                "source_path": metadata.get("source_path", ""),
                "page_number": metadata.get("page_number", "Unknown"),
                "content_preview": doc.page_content[:300] + "..." if len(doc.page_content) > 300 else doc.page_content,
            })
        return sources

    async def ingest_documents(
        self,
        file_paths: Optional[List[str]] = None,
        directory: Optional[str] = None
    ) -> Dict[str, Any]:
        """Ingest PDF documents into the vector store."""
        try:
            # Load documents
            if file_paths:
                documents = self.pdf_loader.load_specific_files(file_paths)
            else:
                documents = self.pdf_loader.load_directory(directory)

            if not documents:
                return {
                    "status": "warning",
                    "documents_processed": 0,
                    "chunks_created": 0,
                    "message": "No documents found to ingest"
                }

            # Split documents
            chunks = self.text_splitter.split_documents(documents)

            # Add to vector store
            self.vector_store.add_documents(chunks)
            self.vector_store.persist()

            return {
                "status": "success",
                "documents_processed": len(documents),
                "chunks_created": len(chunks),
            }

        except Exception as e:
            return {
                "status": "error",
                "documents_processed": 0,
                "chunks_created": 0,
                "message": f"Document ingestion failed: {str(e)}"
            }

    def _get_chat_history(self, session_id: str) -> str:
        """Get formatted chat history for a session."""
        if session_id not in self.sessions:
            return ""
        history = self.sessions[session_id]
        return "\n".join([
            f"Human: {h['question']}\nAssistant: {h['answer']}"
            for h in history[-5:]  # Keep last 5 exchanges
        ])

    def _update_session(self, session_id: str, question: str, answer: str):
        """Update session with new Q&A pair."""
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        self.sessions[session_id].append({"question": question, "answer": answer})
        # Keep only last 10 exchanges
        if len(self.sessions[session_id]) > 10:
            self.sessions[session_id] = self.sessions[session_id][-10:]

    async def clear_session(self, session_id: str):
        """Clear conversation history for a session."""
        if session_id in self.sessions:
            del self.sessions[session_id]

    def clear_all_sessions(self):
        """Clear all conversation sessions."""
        self.sessions.clear()

    def get_collection_stats(self) -> Dict[str, Any]:
        """Get vector store collection statistics."""
        return self.vector_store.get_collection_stats()

    def is_ready(self) -> bool:
        """Check if the service is ready to answer questions."""
        return self.rag_chain is not None

    async def search_documents(
        self,
        query: str,
        top_k: int = None,
        similarity_threshold: Optional[float] = None,
        use_mmr: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Search indexed documents directly via ChromaDB (no Groq/LLM involved)."""
        if not query or not query.strip():
            return {
                "results": [],
                "total": 0,
                "query": query or "",
            }

        k = top_k or settings.TOP_K

        old_k = self.retriever.k
        old_similarity_threshold = self.retriever.similarity_threshold
        old_use_mmr = self.retriever.use_mmr

        async with self._retriever_lock:
            try:
                if top_k:
                    self.retriever.k = top_k
                if similarity_threshold is not None:
                    self.retriever.similarity_threshold = similarity_threshold
                if use_mmr is not None:
                    self.retriever.use_mmr = use_mmr

                if self.retriever.use_mmr:
                    docs = self.vector_store.max_marginal_relevance_search(
                        query, k=k, fetch_k=max(k * 4, 20)
                    )
                else:
                    docs = self.vector_store.similarity_search(query, k=k)

                results = []
                for doc in docs:
                    metadata = doc.metadata if hasattr(doc, 'metadata') else {}
                    results.append({
                        "source_file": metadata.get("source_file", "Unknown"),
                        "page_number": metadata.get("page_number", 0),
                        "content_preview": doc.page_content[:300] + "..." if len(doc.page_content) > 300 else doc.page_content,
                        "similarity_score": None,
                    })

                return {
                    "results": results,
                    "total": len(results),
                    "query": query,
                }
            finally:
                self.retriever.k = old_k
                self.retriever.similarity_threshold = old_similarity_threshold
                self.retriever.use_mmr = old_use_mmr

    async def get_documents_list(self) -> Dict[str, Any]:
        """List all unique indexed PDF documents with metadata from ChromaDB."""
        try:
            result = self.vector_store.get()
            ids = result.get("ids", [])
            metadatas = result.get("metadatas", [])
            documents = result.get("documents", [])

            if not ids:
                return {
                    "documents": [],
                    "total": 0,
                }

            docs_by_file: Dict[str, Dict[str, Any]] = {}
            for i, metadata in enumerate(metadatas):
                source_file = metadata.get("source_file", "Unknown")
                total_pages = metadata.get("total_pages", 0)
                page_number = metadata.get("page_number", 0)

                if source_file not in docs_by_file:
                    docs_by_file[source_file] = {
                        "filename": source_file,
                        "total_pages": total_pages,
                        "chunk_count": 0,
                        "page_numbers": set(),
                    }

                docs_by_file[source_file]["chunk_count"] += 1
                if page_number:
                    docs_by_file[source_file]["page_numbers"].add(page_number)

            doc_list = []
            for source_file, info in sorted(docs_by_file.items()):
                doc_list.append({
                    "filename": info["filename"],
                    "total_pages": info["total_pages"],
                    "chunk_count": info["chunk_count"],
                    "page_numbers": sorted(info["page_numbers"]),
                })

            return {
                "documents": doc_list,
                "total": len(doc_list),
            }
        except Exception as e:
            return {
                "documents": [],
                "total": 0,
            }

    async def delete_vector_database(self, confirm: bool = False) -> Dict[str, Any]:
        """Delete the entire ChromaDB collection.

        Only deletes if confirm=True to prevent accidental deletion.
        Does NOT delete PDF manuals from data/manuals/.
        """
        if not confirm:
            return {
                "status": "error",
                "message": "Deletion not confirmed. Set confirm=true to delete the vector database.",
            }

        try:
            self.vector_store.delete_collection()
            return {
                "status": "success",
                "message": "Vector database collection deleted successfully.",
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to delete vector database: {str(e)}",
            }