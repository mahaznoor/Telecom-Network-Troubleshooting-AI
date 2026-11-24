"""PDF Loader for telecom manuals ingestion using PyMuPDF."""

import fitz  # PyMuPDF
from pathlib import Path
from typing import List, Optional
from langchain_core.documents import Document


class PDFLoader:
    """Load and process PDF documents from telecom manuals.

    Uses PyMuPDF (fitz) for high-quality text extraction with page numbers
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
        source_file = Path(file_path).name

        try:
            doc = fitz.open(file_path)

            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()

                if text.strip():  # Only add non-empty pages
                    metadata = {
                        "source_file": source_file,
                        "source_path": str(file_path),
                        "page_number": page_num + 1,  # 1-based page numbering
                        "total_pages": len(doc),
                    }
                    documents.append(Document(page_content=text, metadata=metadata))

            doc.close()

        except Exception as e:
            raise RuntimeError(f"Failed to load PDF {file_path}: {e}") from e

        return documents

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

        pdf_files = list(target_dir.glob("**/*.pdf"))

        if not pdf_files:
            print(f"No PDF files found in {target_dir}")
            return documents

        for pdf_file in pdf_files:
            try:
                docs = self.load_pdf(str(pdf_file))
                documents.extend(docs)
                print(f"Loaded {len(docs)} pages from {pdf_file.name}")
            except Exception as e:
                print(f"Error loading {pdf_file}: {e}")

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
            try:
                docs = self.load_pdf(file_path)
                documents.extend(docs)
                print(f"Loaded {len(docs)} pages from {Path(file_path).name}")
            except Exception as e:
                print(f"Error loading {file_path}: {e}")

        return documents