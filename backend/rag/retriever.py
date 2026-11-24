"""Retriever module for RAG pipeline."""

from typing import List, Optional, Dict, Any
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from backend.vectorstore.chroma_db import ChromaVectorStore
from backend.config import settings


class TelecomRetriever(BaseRetriever):
    """Custom retriever for telecom troubleshooting documents."""

    vector_store: ChromaVectorStore
    k: int = settings.TOP_K
    similarity_threshold: float = settings.SIMILARITY_THRESHOLD
    use_mmr: bool = True

    def __init__(self, vector_store: ChromaVectorStore, **kwargs):
        super().__init__(vector_store=vector_store, **kwargs)

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager=None
    ) -> List[Document]:
        """Retrieve relevant documents for the query.

        If use_mmr=True, uses Maximum Marginal Relevance for diverse results.
        If use_mmr=False, uses standard similarity search.
        When similarity_threshold is set and scores are available, filters results
        by relevance. Note: ChromaDB MMR does not return scores, so threshold
        filtering with MMR requires a separate scored search for the same query.
        """
        if self.use_mmr:
            # MMR provides diversity but no scores; fetch more candidates for quality
            docs = self.vector_store.max_marginal_relevance_search(
                query, k=self.k, fetch_k=max(self.k * 4, 20)
            )
            # If threshold filtering needed, do a scored search on same query
            # and intersect by content to preserve MMR diversity
            if self.similarity_threshold is not None:
                scored_docs = self.vector_store.similarity_search_with_score(query, k=self.k * 4)
                # ChromaDB returns distance (lower=better for cosine)
                threshold = self.similarity_threshold
                scored_content = {
                    doc.page_content: score
                    for doc, score in scored_docs
                    if score <= threshold
                }
                if scored_content:
                    docs = [d for d in docs if d.page_content in scored_content]
                    # Sort by score to maintain relevance order
                    docs.sort(key=lambda d: scored_content.get(d.page_content, float('inf')))
            return docs

        else:
            # Standard similarity search with optional threshold
            if self.similarity_threshold is not None:
                scored_docs = self.vector_store.similarity_search_with_score(query, k=self.k * 2)
                # ChromaDB returns distance (lower=better)
                filtered = [
                    doc for doc, score in scored_docs
                    if score <= self.similarity_threshold
                ]
                return filtered[:self.k]

            return self.vector_store.similarity_search(query, k=self.k)


def get_retriever(vector_store: ChromaVectorStore) -> TelecomRetriever:
    """Factory function to create a retriever."""
    return TelecomRetriever(vector_store=vector_store)