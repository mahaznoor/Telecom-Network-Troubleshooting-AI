"""Chat service orchestrating the RAG pipeline with Groq LLM."""

import asyncio
import logging
import re
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from backend.ingestion.pdf_loader import PDFLoader
from backend.ingestion.text_splitter import TextSplitter
from backend.ingestion.embeddings import get_embeddings_model
from backend.vectorstore.chroma_db import ChromaVectorStore
from backend.rag.retriever import get_retriever
from backend.rag.chain import get_rag_chain, RAGChain
from backend.config import settings
from backend.services.session_store import ChatSessionStore, SessionOwnershipError


logger = logging.getLogger(__name__)
_GROQ_API_KEY_PATTERN = re.compile(r"\bgsk_[A-Za-z0-9_-]+\b")
_FEEDBACK_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_ -]?key|access[_ -]?token|token|password|secret|authorization)"
    r"\s*[:=]\s*[^\s,;]+"
)
_FEEDBACK_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+\S+")


def _redact_sensitive_values(text: str) -> str:
    """Redact configured and recognizable Groq keys from diagnostic text."""
    if settings.GROQ_API_KEY:
        text = text.replace(settings.GROQ_API_KEY, "[REDACTED_SECRET]")
    return _GROQ_API_KEY_PATTERN.sub("[REDACTED_GROQ_API_KEY]", text)


_CITATION_PATTERN = re.compile(
    r"\[\s*Source:\s*(?P<file>[^\]\[]+?)\s*,\s*Page:\s*(?P<page>[^\]\[,]+?)\s*\]",
    re.IGNORECASE,
)


def strip_unsupported_citations(answer: str, sources: List[Dict[str, Any]]) -> str:
    """Remove [Source: file, Page: N] citations that match no retrieved source."""
    if not answer:
        return answer
    allowed = {
        (str(source.get("source_file", "")).strip().lower(), str(source.get("page_number", "")).strip())
        for source in sources
    }

    def _keep_or_drop(match: "re.Match[str]") -> str:
        key = (match.group("file").strip().lower(), match.group("page").strip())
        return match.group(0) if key in allowed else ""

    cleaned = _CITATION_PATTERN.sub(_keep_or_drop, answer)
    if cleaned == answer:
        return answer
    # Tidy up what removal leaves behind: empty list items and stray spacing.
    cleaned = re.sub(r"(?m)^[ \t]*(?:[-*+]|\d+[.)])[ \t]*$\n?", "", cleaned)
    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r"(?m)[ \t]+$", "", cleaned)
    return cleaned


def _format_exception_for_logging(error: Exception) -> str:
    """Format an exception traceback while removing configured credentials."""
    formatted = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    return _redact_sensitive_values(formatted)


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
        session_db_path = Path(settings.CHROMA_PERSIST_DIR).parent / "chat_history.sqlite3"
        self.session_store = ChatSessionStore(str(session_db_path))
        self._retriever_lock = asyncio.Lock()

    def _initialize_rag_chain(self):
        """Initialize the RAG chain with Groq LLM."""
        try:
            self.rag_chain = get_rag_chain(self.retriever)
        except ValueError as e:
            # API key not configured - chain will be None
            self.rag_chain = None
            logger.warning(
                "RAG chain not initialized (%s)",
                type(e).__name__,
            )

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
        user_id: str,
        session_id: Optional[str] = None,
        top_k: int = None,
        similarity_threshold: Optional[float] = None,
        use_mmr: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Process a troubleshooting question through the RAG pipeline."""
        session_id = self._normalize_session_id(session_id)
        if self.session_store.session_exists_for_another_user(session_id, user_id):
            raise SessionOwnershipError("Conversation not found or unavailable.")

        # Validate input
        if not question or not question.strip():
            return {
                "answer": "Please provide a valid question.",
                "sources": [],
                "session_id": session_id,
                "error": "Empty question"
            }

        # Ensure RAG chain is available
        try:
            rag_chain = self._ensure_rag_chain()
        except RuntimeError as e:
            return {
                "answer": str(e),
                "sources": [],
                "session_id": session_id,
                "error": "configuration_error"
            }

        # Get chat history for session
        chat_history = self._get_chat_history(session_id, user_id)

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
                if top_k is not None:
                    self.retriever.k = top_k
                if similarity_threshold is not None:
                    self.retriever.similarity_threshold = similarity_threshold
                if use_mmr is not None:
                    self.retriever.use_mmr = use_mmr

                # Invoke RAG chain while holding the lock
                result = await rag_chain.ainvoke(question, chat_history)

                # Check if context is empty
                context_docs = result.get("context", [])
                answer = result.get("answer") or (
                    "I couldn't find enough information in the retrieved manuals to answer that."
                )
                sources = self._format_sources(context_docs)
                answer = strip_unsupported_citations(answer, sources)

                # Update session history
                message_id = self._update_session(
                    session_id, user_id, question, answer, sources
                )

                return {
                    "answer": answer,
                    "sources": sources,
                    "session_id": session_id,
                    "message_id": message_id,
                }
            except Exception as e:
                logger.error(
                    "Chat processing failed:\n%s",
                    _format_exception_for_logging(e),
                )
                error_msg = _redact_sensitive_values(str(e))
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
        documents_processed = 0
        chunks_created = 0
        write_started = False
        try:
            if file_paths is not None and not file_paths:
                return {
                    "status": "warning",
                    "documents_processed": 0,
                    "chunks_created": 0,
                    "message": "No PDF files were provided.",
                }

            # Load documents
            if file_paths is not None:
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

            document_hashes = [
                document.metadata.get("file_hash")
                for document in documents
            ]
            if any(not isinstance(file_hash, str) or not file_hash for file_hash in document_hashes):
                raise ValueError("Loaded PDFs are missing content fingerprints")
            file_hashes = set(document_hashes)

            existing_metadata = self.vector_store.get_all_metadata().get("metadatas") or []
            existing_hashes = {
                metadata.get("file_hash")
                for metadata in existing_metadata
                if isinstance(metadata, dict) and metadata.get("file_hash")
            }
            new_file_hashes = file_hashes - existing_hashes
            input_sources = set()
            accepted_source_by_hash = {}
            for document in documents:
                file_hash = document.metadata["file_hash"]
                source_path = document.metadata.get("source_path")
                source_file = document.metadata.get("source_file")
                source_identity = (
                    source_path
                    if isinstance(source_path, str) and source_path
                    else source_file
                )
                input_sources.add((file_hash, source_identity))
                if file_hash in new_file_hashes:
                    accepted_source_by_hash.setdefault(file_hash, source_identity)

            duplicate_file_count = len(input_sources) - len(new_file_hashes)
            if not new_file_hashes:
                return {
                    "status": "warning",
                    "documents_processed": 0,
                    "chunks_created": 0,
                    "message": "All selected PDFs are already indexed.",
                }

            documents = [
                document for document in documents
                if document.metadata.get("file_hash") in new_file_hashes
                and (
                    document.metadata.get("source_path")
                    or document.metadata.get("source_file")
                ) == accepted_source_by_hash[document.metadata["file_hash"]]
            ]
            documents_processed = len(new_file_hashes)

            # Split documents
            chunks = self.text_splitter.split_documents(documents)
            if not chunks:
                return {
                    "status": "warning",
                    "documents_processed": 0,
                    "chunks_created": 0,
                    "message": "No text chunks were created from the selected PDFs.",
                }

            # Add to vector store
            write_started = True
            added_ids = self.vector_store.add_documents(chunks)
            chunks_created = len(added_ids)
            if chunks_created != len(chunks):
                raise RuntimeError(
                    f"Vector store accepted {chunks_created} of {len(chunks)} chunks"
                )
            self.vector_store.persist()

            result = {
                "status": "success",
                "documents_processed": documents_processed,
                "chunks_created": chunks_created,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            if duplicate_file_count:
                result["message"] = f"Skipped {duplicate_file_count} duplicate PDF(s)."
            return result

        except Exception as e:
            message = f"Document ingestion failed: {e}"
            if write_started:
                message += " The vector-store write may be incomplete; verify the document list before retrying."
            return {
                "status": "error",
                "documents_processed": documents_processed if chunks_created else 0,
                "chunks_created": chunks_created,
                "message": message,
            }

    @staticmethod
    def _normalize_session_id(session_id: Optional[str]) -> str:
        """Return a canonical UUID, replacing missing or invalid IDs."""
        try:
            return str(uuid.UUID(str(session_id)))
        except (ValueError, TypeError, AttributeError):
            return str(uuid.uuid4())

    @staticmethod
    def is_valid_session_id(session_id: str) -> bool:
        """Check whether a session identifier is a UUID."""
        try:
            uuid.UUID(session_id)
            return True
        except (ValueError, TypeError, AttributeError):
            return False

    def _get_chat_history(self, session_id: str, user_id: str) -> str:
        """Get formatted chat history for a session."""
        if session_id not in self.sessions:
            messages = self.session_store.get_messages(session_id, user_id)
            exchanges = []
            for index in range(0, len(messages) - 1, 2):
                user_message = messages[index]
                assistant_message = messages[index + 1]
                if user_message["role"] == "user" and assistant_message["role"] == "assistant":
                    exchanges.append({
                        "question": user_message["content"],
                        "answer": assistant_message["content"],
                    })
            self.sessions[session_id] = exchanges
        history = self.sessions[session_id]
        return "\n".join([
            f"Human: {h['question']}\nAssistant: {h['answer']}"
            for h in history[-5:]  # Keep last 5 exchanges
        ])

    def _update_session(
        self,
        session_id: str,
        user_id: str,
        question: str,
        answer: str,
        sources: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Update session with new Q&A pair."""
        message_id = self.session_store.save_exchange(
            session_id, user_id, question, answer, sources or []
        )
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        self.sessions[session_id].append({"question": question, "answer": answer})
        # Keep only last 10 exchanges
        if len(self.sessions[session_id]) > 10:
            self.sessions[session_id] = self.sessions[session_id][-10:]
        return message_id

    async def clear_session(self, session_id: str, user_id: str) -> bool:
        """Delete a conversation and its persisted messages."""
        normalized_id = self._normalize_session_id(session_id) if self.is_valid_session_id(session_id) else None
        if normalized_id is None:
            return False
        self.sessions.pop(normalized_id, None)
        return self.session_store.delete_session(normalized_id, user_id)

    def clear_all_sessions(self, user_id: str):
        """Clear only the authenticated user's conversation sessions."""
        self.sessions.clear()
        self.session_store.clear_all(user_id)

    def list_chat_sessions(
        self,
        user_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List saved conversations, newest activity first."""
        return self.session_store.list_sessions(user_id, limit)

    def get_chat_session(
        self,
        session_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a saved conversation, returning None for invalid or unknown IDs."""
        if not self.is_valid_session_id(session_id):
            return None
        normalized_id = self._normalize_session_id(session_id)
        session = self.session_store.get_session(normalized_id, user_id)
        if session is None:
            return None
        allowed_source_fields = {
            "source_file",
            "page_number",
            "content_preview",
            "similarity_score",
        }
        for message in session.get("messages", []):
            sources = message.get("sources")
            if isinstance(sources, list):
                message["sources"] = [
                    {
                        key: value
                        for key, value in source.items()
                        if key in allowed_source_fields
                    }
                    for source in sources
                    if isinstance(source, dict)
                ]
        return session

    def submit_feedback(
        self,
        session_id: str,
        user_id: str,
        message_id: int,
        rating: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update feedback for a persisted assistant response."""
        if comment is not None:
            comment = _redact_sensitive_values(comment)
            comment = _FEEDBACK_SECRET_ASSIGNMENT_PATTERN.sub(
                "[REDACTED_SECRET]", comment
            )
            comment = _FEEDBACK_BEARER_PATTERN.sub("Bearer [REDACTED_SECRET]", comment)
        return self.session_store.submit_feedback(
            session_id=session_id,
            user_id=user_id,
            message_id=message_id,
            rating=rating,
            comment=comment,
        )

    def get_feedback_summary(
        self,
        limit: int = 20,
        rating: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get aggregate counts and recent response feedback."""
        return self.session_store.get_feedback_summary(limit=limit, rating=rating)

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

        async with self._retriever_lock:
            old_k = self.retriever.k
            old_similarity_threshold = self.retriever.similarity_threshold
            old_use_mmr = self.retriever.use_mmr
            try:
                if top_k is not None:
                    self.retriever.k = top_k
                if similarity_threshold is not None:
                    self.retriever.similarity_threshold = similarity_threshold
                if use_mmr is not None:
                    self.retriever.use_mmr = use_mmr

                docs = self.retriever.invoke(query)

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
        result = self.vector_store.get_all_metadata()
        ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []

        docs_by_file: Dict[tuple, Dict[str, Any]] = {}
        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else None
            metadata = metadata if isinstance(metadata, dict) else {}

            source_path = metadata.get("source_path")
            source_file = metadata.get("source_file")
            if not isinstance(source_path, str) or not source_path:
                source_path = None
            if not isinstance(source_file, str) or not source_file or source_file == "Unknown":
                source_file = Path(source_path).name if source_path else "Unknown document"

            ingested_at = metadata.get("ingested_at")
            if not isinstance(ingested_at, str) or not ingested_at:
                ingested_at = None
            identity = source_path or source_file
            key = (identity, ingested_at) if ingested_at else (identity, None)

            if key not in docs_by_file:
                docs_by_file[key] = {
                    "filename": source_file,
                    "document_id": None,
                    "total_pages": 0,
                    "file_size_bytes": None,
                    "ingested_at": ingested_at,
                    "chunk_count": 0,
                    "page_numbers": set(),
                }

            info = docs_by_file[key]
            info["chunk_count"] += 1
            file_hash = metadata.get("file_hash")
            if isinstance(file_hash, str) and re.fullmatch(r"[0-9a-f]{64}", file_hash):
                if info["document_id"] is None:
                    info["document_id"] = file_hash
                elif info["document_id"] != file_hash:
                    info["document_id"] = None

            try:
                total_pages = int(metadata.get("total_pages") or 0)
            except (TypeError, ValueError):
                total_pages = 0
            info["total_pages"] = max(info["total_pages"], total_pages)

            file_size_bytes = metadata.get("file_size_bytes")
            if (
                info["file_size_bytes"] is None
                and isinstance(file_size_bytes, int)
                and not isinstance(file_size_bytes, bool)
                and file_size_bytes >= 0
            ):
                info["file_size_bytes"] = file_size_bytes

            try:
                page_number = int(metadata.get("page_number") or 0)
            except (TypeError, ValueError):
                page_number = 0
            if page_number > 0:
                info["page_numbers"].add(page_number)

        doc_list = [
            {
                "filename": info["filename"],
                "document_id": info["document_id"],
                "total_pages": info["total_pages"],
                "file_size_bytes": info["file_size_bytes"],
                "ingested_at": info["ingested_at"],
                "chunk_count": info["chunk_count"],
                "page_numbers": sorted(info["page_numbers"]),
            }
            for info in docs_by_file.values()
        ]
        doc_list.sort(key=lambda info: (info["filename"].casefold(), info["ingested_at"] or ""))

        return {
            "documents": doc_list,
            "total": len(doc_list),
        }

    def delete_document(self, document_id: str) -> int:
        """Remove indexed chunks for one content fingerprint."""
        if not re.fullmatch(r"[0-9a-f]{64}", document_id):
            raise ValueError("Invalid document identifier.")
        return self.vector_store.delete_documents_by_file_hash(document_id)

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