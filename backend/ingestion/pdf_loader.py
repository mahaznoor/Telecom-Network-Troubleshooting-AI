"""PDF Loader for telecom manuals ingestion using PyMuPDF."""

import hashlib
import pymupdf
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from langchain_core.documents import Document


class PDFLoader:
    """Load and process PDF documents from telecom manuals.

    Uses PyMuPDF for high-quality text extraction with page numbers
    and metadata preservation.
    """

    def __init__(self, data_dir: str = "./data/manuals"):
        self.data_dir = Path(data_dir)

    def load_pdf(self, file_path: str) -> List[Document]:
        """Load a single PDF file and extract text with page numbers.

        Args:
            file_path: Path to the PDF file.

        Returns:
            List of Document objects with page content and metadata.
        """
        documents = []
        path = Path(file_path)
        source_file = path.name

        try:
            if path.suffix.lower() != ".pdf":
                raise ValueError("File must have a .pdf extension")
            if not path.is_file():
                raise FileNotFoundError("File does not exist")

            file_size_bytes = path.stat().st_size
            if file_size_bytes == 0:
                raise ValueError("PDF file is empty")

            file_hash = self._get_file_hash(path)
            ingested_at = datetime.now(timezone.utc).isoformat()
            doc = pymupdf.open(file_path)
            try:
                total_pages = len(doc)
                if total_pages == 0:
                    raise ValueError("PDF contains no pages")

                for page_num in range(total_pages):
                    page = doc[page_num]
                    text = page.get_text()

                    if text.strip():
                        metadata = {
                            "source_file": source_file,
                            "source_path": str(file_path),
                            "page_number": page_num + 1,
                            "total_pages": total_pages,
                            "file_size_bytes": file_size_bytes,
                            "file_hash": file_hash,
                            "ingested_at": ingested_at,
                        }
                        documents.append(Document(page_content=text, metadata=metadata))
            finally:
                doc.close()

            if not documents:
                raise ValueError("PDF contains no extractable text")

        except Exception as e:
            raise RuntimeError(f"Failed to load PDF {file_path}: {e}") from e

        return documents

    @staticmethod
    def _get_file_hash(path: Path) -> str:
        """Calculate a file fingerprint without loading the entire PDF into memory."""
        digest = hashlib.sha256()
        with path.open("rb") as pdf_file:
            for block in iter(lambda: pdf_file.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def load_directory(self, directory: Optional[str] = None) -> List[Document]:
        """Load all PDF files from a directory recursively.

        Args:
            directory: Optional directory path. Defaults to self.data_dir.

        Returns:
            List of Document objects from all PDFs.
        """
        target_dir = Path(directory) if directory else self.data_dir
        documents = []

        if not target_dir.exists():
            raise FileNotFoundError(f"Directory not found: {target_dir}")
        if not target_dir.is_dir():
            raise NotADirectoryError(f"Not a directory: {target_dir}")

        pdf_files = sorted(
            path for path in target_dir.rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        )

        if not pdf_files:
            return documents

        for pdf_file in pdf_files:
            documents.extend(self.load_pdf(str(pdf_file)))

        return documents

    def load_specific_files(self, file_paths: List[str]) -> List[Document]:
        """Load specific PDF files.

        Args:
            file_paths: List of PDF file paths.

        Returns:
            List of Document objects from specified files.
        """
        documents = []

        for file_path in file_paths:
            documents.extend(self.load_pdf(file_path))

        return documents