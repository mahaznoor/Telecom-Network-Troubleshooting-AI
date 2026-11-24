"""Text splitter for document chunking with metadata preservation."""

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from typing import List
from backend.config import settings


class TextSplitter:
    """Split documents into chunks for embedding and retrieval.

    Preserves source metadata (source_file, page_number) in each chunk.
    """

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None
    ):
        self.chunk_size = chunk_size or settings.CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", " ", ""],
            add_start_index=True,  # Track chunk position in original doc
        )

    def split_documents(self, documents: List[Document]) -> List[Document]:
        """Split documents into chunks while preserving metadata.

        Args:
            documents: List of Document objects to split.

        Returns:
            List of chunked Document objects with preserved metadata.
        """
        chunks = self.splitter.split_documents(documents)

        # Ensure metadata is preserved and add chunk index
        for i, chunk in enumerate(chunks):
            if "source_file" not in chunk.metadata:
                chunk.metadata["source_file"] = "unknown"
            if "page_number" not in chunk.metadata:
                chunk.metadata["page_number"] = 0
            chunk.metadata["chunk_index"] = i

        return chunks

    def split_text(self, text: str) -> List[str]:
        """Split raw text into chunks.

        Args:
            text: Raw text to split.

        Returns:
            List of text chunks.
        """
        return self.splitter.split_text(text)