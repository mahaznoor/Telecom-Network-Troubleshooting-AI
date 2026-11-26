"""Focused tests for document metadata, listing, and frontend display."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.api.routes import list_documents
from backend.ingestion.pdf_loader import PDFLoader
from backend.services.chat_service import ChatService
from backend.vectorstore.chroma_db import ChromaVectorStore
from frontend.components.document_metadata import (
    build_document_rows,
    filter_documents,
    format_file_size,
    format_ingested_at,
)
from streamlit.testing.v1 import AppTest

from backend.api.auth import AuthUser


ENTRYPOINT = Path(__file__).resolve().parents[1] / "frontend" / "streamlit_app.py"


class FakePdfPage:
    def get_text(self) -> str:
        return "Page content"


class FakePdfDocument:
    def __init__(self) -> None:
        self.pages = [FakePdfPage(), FakePdfPage()]
        self.closed = False

    def __len__(self) -> int:
        return len(self.pages)

    def __getitem__(self, index: int) -> FakePdfPage:
        return self.pages[index]

    def close(self) -> None:
        self.closed = True


class DocumentListingTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_groups_chunks_by_source_and_reports_metadata(self) -> None:
        fingerprint = "a" * 64
        service = object.__new__(ChatService)
        service.vector_store = SimpleNamespace(
            get_all_metadata=lambda: {
                "ids": ["page-1", "page-2", "old-page", "duplicate-file", "unknown-1", "unknown-2"],
                "metadatas": [
                    {
                        "source_file": "guide.pdf",
                        "source_path": "C:\\manuals\\guide.pdf",
                        "file_hash": fingerprint,
                        "page_number": 1,
                        "total_pages": 2,
                        "file_size_bytes": 2048,
                        "ingested_at": "2026-01-02T03:04:05+00:00",
                    },
                    {
                        "source_file": "guide.pdf",
                        "source_path": "C:\\manuals\\guide.pdf",
                        "file_hash": fingerprint,
                        "page_number": 2,
                        "total_pages": 2,
                        "file_size_bytes": 2048,
                        "ingested_at": "2026-01-02T03:04:05+00:00",
                    },
                    {
                        "source_file": "legacy.pdf",
                        "source_path": "C:\\manuals\\legacy.pdf",
                        "page_number": 1,
                    },
                    {
                        "source_file": "guide.pdf",
                        "source_path": "D:\\other\\guide.pdf",
                        "page_number": 3,
                        "total_pages": 3,
                    },
                    None,
                    {"page_number": "not-a-number"},
                ],
            }
        )

        result = await service.get_documents_list()

        self.assertEqual(result["total"], 4)
        guide = next(
            document
            for document in result["documents"]
            if document["filename"] == "guide.pdf" and document["total_pages"] == 2
        )
        duplicate = next(
            document
            for document in result["documents"]
            if document["filename"] == "guide.pdf" and document["total_pages"] == 3
        )
        legacy = next(document for document in result["documents"] if document["filename"] == "legacy.pdf")
        unknown = next(
            document
            for document in result["documents"]
            if document["filename"] == "Unknown document"
        )
        self.assertEqual(guide["filename"], "guide.pdf")
        self.assertEqual(guide["total_pages"], 2)
        self.assertEqual(guide["chunk_count"], 2)
        self.assertEqual(guide["page_numbers"], [1, 2])
        self.assertEqual(guide["file_size_bytes"], 2048)
        self.assertEqual(guide["ingested_at"], "2026-01-02T03:04:05+00:00")
        self.assertEqual(guide["document_id"], fingerprint)
        self.assertEqual(duplicate["filename"], "guide.pdf")
        self.assertEqual(duplicate["chunk_count"], 1)
        self.assertEqual(legacy["file_size_bytes"], None)
        self.assertEqual(legacy["ingested_at"], None)
        self.assertEqual(unknown["filename"], "Unknown document")
        self.assertEqual(unknown["chunk_count"], 2)
        self.assertEqual(unknown["page_numbers"], [])

    async def test_document_listing_does_not_turn_storage_errors_into_empty_results(self) -> None:
        def fail_to_read():
            raise RuntimeError("metadata read failed")

        service = object.__new__(ChatService)
        service.vector_store = SimpleNamespace(get_all_metadata=fail_to_read)

        with self.assertRaisesRegex(RuntimeError, "metadata read failed"):
            await service.get_documents_list()

    async def test_documents_route_accepts_optional_legacy_metadata(self) -> None:
        class DocumentServiceStub:
            async def get_documents_list(self) -> dict:
                return {
                    "documents": [
                        {
                            "filename": "legacy.pdf",
                            "total_pages": 0,
                            "chunk_count": 1,
                            "page_numbers": [],
                            "file_size_bytes": None,
                            "ingested_at": None,
                        }
                    ],
                    "total": 1,
                }

        response = await list_documents(
            current_user=AuthUser(
                user_id="00000000-0000-0000-0000-000000000001",
                email="user@example.com",
                role="user",
                created_at="2026-01-01T00:00:00+00:00",
            ),
            chat_service=DocumentServiceStub(),
        )

        self.assertEqual(response.total, 1)
        self.assertIsNone(response.documents[0].file_size_bytes)
        self.assertIsNone(response.documents[0].ingested_at)


class DocumentMetadataTests(unittest.TestCase):
    def test_pdf_loader_stores_original_size_and_ingestion_time(self) -> None:
        fake_pdf = FakePdfDocument()
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "manual.pdf"
            pdf_path.write_bytes(b"test pdf bytes")

            with patch("backend.ingestion.pdf_loader.pymupdf.open", return_value=fake_pdf):
                pages = PDFLoader().load_pdf(str(pdf_path))

        self.assertTrue(fake_pdf.closed)
        self.assertEqual(len(pages), 2)
        metadata = pages[0].metadata
        self.assertEqual(metadata["source_file"], "manual.pdf")
        self.assertEqual(metadata["file_size_bytes"], len(b"test pdf bytes"))
        self.assertEqual(metadata["total_pages"], 2)
        self.assertIsInstance(datetime.fromisoformat(metadata["ingested_at"]), datetime)

    def test_chroma_metadata_reader_requests_only_metadata(self) -> None:
        class FakeChroma:
            def __init__(self) -> None:
                self.include = None

            def get(self, include: list) -> dict:
                self.include = include
                return {"ids": ["chunk-1"], "metadatas": [{"source_file": "manual.pdf"}]}

        vector_store = object.__new__(ChromaVectorStore)
        vector_store.vector_store = FakeChroma()

        result = vector_store.get_all_metadata()

        self.assertEqual(vector_store.vector_store.include, ["metadatas"])
        self.assertEqual(result["ids"], ["chunk-1"])

    def test_chroma_document_deletion_targets_only_the_content_fingerprint(self) -> None:
        class FakeChroma:
            def __init__(self) -> None:
                self.where = None
                self.deleted_ids = []

            def get(self, where, include):
                self.where = where
                return {"ids": ["chunk-1", "chunk-2"]}

            def delete(self, ids):
                self.deleted_ids = ids

        vector_store = object.__new__(ChromaVectorStore)
        vector_store.vector_store = FakeChroma()
        fingerprint = "b" * 64

        with patch.object(vector_store, "persist") as persist:
            deleted = vector_store.delete_documents_by_file_hash(fingerprint)

        self.assertEqual(deleted, 2)
        self.assertEqual(
            vector_store.vector_store.where,
            {"file_hash": fingerprint},
        )
        self.assertEqual(
            vector_store.vector_store.deleted_ids,
            ["chunk-1", "chunk-2"],
        )
        persist.assert_called_once_with()

    def test_chroma_document_deletion_rejects_invalid_fingerprints(self) -> None:
        class FakeChroma:
            def get(self, **kwargs):
                raise AssertionError("Invalid identifiers must not query Chroma.")

        vector_store = object.__new__(ChromaVectorStore)
        vector_store.vector_store = FakeChroma()

        with self.assertRaises(ValueError):
            vector_store.delete_documents_by_file_hash("../manual.pdf")

    def test_frontend_formats_and_filters_document_metadata_with_legacy_fallbacks(self) -> None:
        documents = [
            {
                "filename": "Router-Guide.pdf",
                "file_size_bytes": 1536,
                "total_pages": 18,
                "chunk_count": 24,
                "ingested_at": "2026-01-02T03:04:05+00:00",
            },
            {
                "filename": "Legacy.pdf",
                "total_pages": 0,
                "chunk_count": 2,
            },
        ]

        rows = build_document_rows(filter_documents(documents, "ROUTER"))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["File name"], "Router-Guide.pdf")
        self.assertEqual(rows[0]["File size"], "1.5 KB")
        self.assertEqual(rows[0]["Pages"], "18")
        self.assertEqual(rows[0]["Indexed chunks"], "24")
        self.assertEqual(rows[0]["Ingested"], "2026-01-02 03:04:05 +0000")
        legacy_row = build_document_rows(documents[1:])[0]
        self.assertEqual(legacy_row["File size"], "Unknown")
        self.assertEqual(legacy_row["Pages"], "Unknown")
        self.assertEqual(legacy_row["Ingested"], "Unknown")
        self.assertEqual(format_file_size(None), "Unknown")
        self.assertEqual(format_ingested_at("not-a-timestamp"), "not-a-timestamp")
        self.assertEqual(filter_documents(documents, "no-match"), [])

    def test_knowledge_base_renders_and_filters_backend_document_list(self) -> None:
        documents = [
            {
                "filename": "Router-Guide.pdf",
                "total_pages": 18,
                "file_size_bytes": 1536,
                "chunk_count": 24,
                "page_numbers": list(range(1, 19)),
                "ingested_at": "2026-01-02T03:04:05+00:00",
            },
            {
                "filename": "Legacy.pdf",
                "total_pages": 0,
                "file_size_bytes": None,
                "chunk_count": 2,
                "page_numbers": [],
                "ingested_at": None,
            },
        ]
        app = AppTest.from_file(str(ENTRYPOINT), default_timeout=60)
        app.session_state["current_page"] = "knowledge_base"
        app.session_state["access_token"] = "test-user-token"
        app.session_state["current_user"] = {
            "email": "user@example.com",
            "role": "user",
        }

        with patch(
            "frontend.pages.knowledge_base.get_documents_list",
            return_value={"documents": documents, "total": len(documents)},
        ):
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe), 1)
            self.assertEqual(len(app.dataframe[0].value), 2)

            app.text_input(key="kb_document_filter").set_value("legacy").run()
            self.assertFalse(app.exception)
            rows = app.dataframe[0].value
            self.assertEqual(rows["File name"].tolist(), ["Legacy.pdf"])
            self.assertEqual(rows["Pages"].tolist(), ["Unknown"])
            self.assertEqual(rows["File size"].tolist(), ["Unknown"])

    def test_knowledge_base_reports_listing_errors_instead_of_empty_state(self) -> None:
        app = AppTest.from_file(str(ENTRYPOINT), default_timeout=60)
        app.session_state["current_page"] = "knowledge_base"
        app.session_state["access_token"] = "test-user-token"
        app.session_state["current_user"] = {
            "email": "user@example.com",
            "role": "user",
        }

        with patch(
            "frontend.pages.knowledge_base.get_documents_list",
            return_value={"error": "HTTP 500: document listing failed"},
        ):
            app.run()

        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertIn("document listing failed", app.error[0].value)

    def test_admin_can_confirm_index_removal_from_knowledge_base_page(self) -> None:
        document_id = "c" * 64
        document = {
            "filename": "Router-Guide.pdf",
            "document_id": document_id,
            "total_pages": 2,
            "file_size_bytes": 1024,
            "chunk_count": 4,
            "page_numbers": [1, 2],
            "ingested_at": "2026-01-02T03:04:05+00:00",
        }
        app = AppTest.from_file(str(ENTRYPOINT), default_timeout=60)
        app.session_state["current_page"] = "knowledge_base"
        app.session_state["access_token"] = "test-admin-token"
        app.session_state["current_user"] = {
            "email": "admin@example.com",
            "role": "admin",
        }

        with (
            patch(
                "frontend.components.sidebar.list_chat_sessions",
                return_value={"sessions": []},
            ),
            patch(
                "frontend.pages.knowledge_base.get_documents_list",
                return_value={"documents": [document], "total": 1},
            ),
            patch(
                "frontend.pages.knowledge_base.delete_indexed_document",
                return_value={
                    "message": "The document was removed from the index."
                },
            ) as delete_document,
            patch("frontend.pages.knowledge_base.st.rerun"),
        ):
            app.run()
            app.button(key="kb_request_document_deletion").click().run()
            app.button(key="kb_confirm_document_deletion").click().run()

        delete_document.assert_called_once_with(document_id)
        self.assertFalse(app.exception)


if __name__ == "__main__":
    unittest.main()
