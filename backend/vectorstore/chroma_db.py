"""ChromaDB vector store for document storage and retrieval."""

import os
from typing import List, Optional, Dict, Any
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from backend.config import settings
from backend.ingestion.embeddings import get_embeddings_model


class ChromaVectorStore:
    """ChromaDB vector store wrapper for telecom manuals.

    Handles document storage, similarity search, and retrieval
    with HuggingFace BGE embeddings.
    """

    def __init__(
        self,
        persist_dir: str = None,
        collection_name: str = None,
        embedding_model: Embeddings = None
    ):
        self.persist_dir = persist_dir or settings.CHROMA_PERSIST_DIR
        self.collection_name = collection_name or settings.CHROMA_COLLECTION_NAME
        self.embedding_model = embedding_model or get_embeddings_model()

        os.makedirs(self.persist_dir, exist_ok=True)

        self.vector_store = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.embedding_model,
            persist_directory=self.persist_dir,
        )

    def add_documents(self, documents: List[Document]) -> List[str]:
        """Add documents to the vector store.

        Args:
            documents: List of Document objects to add.

        Returns:
            List of document IDs.
        """
        if not documents:
            return []

        try:
            return self.vector_store.add_documents(documents)
        except Exception as e:
            raise RuntimeError(f"Failed to add documents to ChromaDB: {e}") from e

    def similarity_search(
        self,
        query: str,
        k: int = None,
        filter: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """Search for similar documents.

        Args:
            query: Search query text.
            k: Number of results to return.
            filter: Optional metadata filter.

        Returns:
            List of similar Document objects.
        """
        k = k or settings.TOP_K
        try:
            return self.vector_store.similarity_search(query, k=k, filter=filter)
        except Exception as e:
            raise RuntimeError(f"Similarity search failed: {e}") from e

    def similarity_search_with_score(
        self,
        query: str,
        k: int = None,
        filter: Optional[Dict[str, Any]] = None
    ) -> List[tuple[Document, float]]:
        """Search for similar documents with similarity scores.

        Args:
            query: Search query text.
            k: Number of results to return.
            filter: Optional metadata filter.

        Returns:
            List of (Document, score) tuples. Score is distance (lower=better).
        """
        k = k or settings.TOP_K
        try:
            return self.vector_store.similarity_search_with_score(query, k=k, filter=filter)
        except Exception as e:
            raise RuntimeError(f"Similarity search with score failed: {e}") from e

    def max_marginal_relevance_search(
        self,
        query: str,
        k: int = None,
        fetch_k: int = 20,
        filter: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """Search using Maximum Marginal Relevance for diverse results.

        Args:
            query: Search query text.
            k: Number of results to return.
            fetch_k: Number of candidates to fetch before MMR.
            filter: Optional metadata filter.

        Returns:
            List of diverse Document objects.
        """
        k = k or settings.TOP_K
        try:
            return self.vector_store.max_marginal_relevance_search(
                query, k=k, fetch_k=fetch_k, filter=filter
            )
        except Exception as e:
            raise RuntimeError(f"MMR search failed: {e}") from e

    def delete_collection(self) -> None:
        """Delete the entire collection."""
        try:
            self.vector_store.delete_collection()
        except Exception as e:
            raise RuntimeError(f"Failed to delete collection: {e}") from e

    def get_collection_stats(self) -> Dict[str, Any]:
        """Get collection statistics.

        Returns:
            Dictionary with collection info.
        """
        try:
            # Use public get() API to avoid private _collection access
            result = self.vector_store.get()
            count = len(result.get('ids', []))
            return {
                "collection_name": self.collection_name,
                "document_count": count,
                "persist_dir": self.persist_dir,
                "embedding_model": settings.EMBEDDING_MODEL,
                "embedding_dimension": settings.EMBEDDING_DIMENSION,
            }
        except Exception as e:
            return {"error": str(e)}

    def persist(self) -> None:
        """Persist the vector store to disk.
        
        Note: In newer versions of langchain-chroma, persistence is automatic
        when using persist_directory. This method is kept for compatibility.
        """
        try:
            # Newer versions of Chroma auto-persist; no-op if persist() not available
            if hasattr(self.vector_store, 'persist'):
                self.vector_store.persist()
        except Exception as e:
            raise RuntimeError(f"Failed to persist vector store: {e}") from e