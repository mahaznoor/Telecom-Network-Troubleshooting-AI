"""Focused tests for PDF validation, duplicate handling, and ingestion failures."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests
from langchain_core.documents import Document

from backend.ingestion.pdf_loader import PDFLoader
from backend.services.chat_service import ChatService
from frontend.components.api_client import (
    API_BASE_URL,
    call_api,
    ingest_documents as api_ingest_documents,
    upload_documents as api_upload_documents,
)
from streamlit.testing.v1 import AppTest


ENTRYPOINT = Path(__file__).resolve().parents[1] / "frontend" / "streamlit_app.py"


class FakePdfPage:
    def get_text(self) -> str:
        return "Extracted PDF text"


class FakePdfDocument:
    def __init__(self) -> None:
        self.pages = [FakePdfPage()]
        self.closed = False

    def __len__(self) -> int:
        return len(self.pages)

    def __getitem__(self, index: int) -> FakePdfPage:
        return self.pages[index]

    def close(self) -> None:
        self.closed = True


def make_document(file_hash: str, source_path: str, page_number: int = 1) -> Document:
    return Document(
        page_content=f"Text for page {page_number}",
        metadata={
            "file_hash": file_hash,
            "source_path": source_path,
            "source_file": Path(source_path).name,
            "page_number": page_number,
            "total_pages": 2,
        },
    )


class PdfValidationTests(unittest.TestCase):
    def test_rejects_non_pdf_and_empty_files_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            text_path = Path(temp_dir) / "manual.txt"
            text_path.write_text("not a PDF")
            empty_path = Path(temp_dir) / "empty.pdf"
            empty_path.touch()

            with patch("backend.ingestion.pdf_loader.pymupdf.open") as open_pdf:
                with self.assertRaisesRegex(RuntimeError, "must have a .pdf extension"):
                    PDFLoader().load_pdf(str(text_path))
                with self.assertRaisesRegex(RuntimeError, "PDF file is empty"):
                    PDFLoader().load_pdf(str(empty_path))
                open_pdf.assert_not_called()

    def test_records_file_hash_and_closes_pdf(self) -> None:
        contents = b"mock pdf bytes"
        pdf_document = FakePdfDocument()
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "manual.pdf"
            pdf_path.write_bytes(contents)

            with patch("backend.ingestion.pdf_loader.pymupdf.open", return_value=pdf_document):
                pages = PDFLoader().load_pdf(str(pdf_path))

        self.assertTrue(pdf_document.closed)
        self.assertEqual(
            pages[0].metadata["file_hash"],
            hashlib.sha256(contents).hexdigest(),
        )

class FakePdfLoader:
    def __init__(self, documents=None, error=None) -> None:
        self.documents = documents or []
        self.error = error
        self.calls = []

    def load_specific_files(self, file_paths):
        self.calls.append(("files", file_paths))
        if self.error:
            raise self.error
        return self.documents

    def load_directory(self, directory):
        self.calls.append(("directory", directory))
        if self.error:
            raise self.error
        return self.documents


class FakeTextSplitter:
    def split_documents(self, documents):
        return documents


class FakeVectorStore:
    def __init__(self, metadata=None, add_error=None) -> None:
        self.metadata = metadata or []
        self.add_error = add_error
        self.added_documents = []
        self.persist_called = False

    def get_all_metadata(self):
        return {"metadatas": self.metadata}

    def add_documents(self, documents):
        if self.add_error:
            raise self.add_error
        self.added_documents = documents
        return [f"chunk-{index}" for index in range(len(documents))]

    def persist(self):
        self.persist_called = True


def make_chat_service(documents=None, existing_metadata=None, loader_error=None, add_error=None):
    service = object.__new__(ChatService)
    service.pdf_loader = FakePdfLoader(documents, loader_error)
    service.text_splitter = FakeTextSplitter()
    service.vector_store = FakeVectorStore(existing_metadata, add_error)
    return service


class IngestionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_file_list_does_not_fall_back_to_directory_ingestion(self) -> None:
        service = make_chat_service()

        result = await service.ingest_documents(file_paths=[])

        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["documents_processed"], 0)
        self.assertEqual(service.pdf_loader.calls, [])
        self.assertEqual(service.vector_store.added_documents, [])

    async def test_already_indexed_content_is_skipped(self) -> None:
        service = make_chat_service(
            documents=[make_document("same-hash", "C:\\uploads\\manual.pdf")],
            existing_metadata=[{"file_hash": "same-hash"}],
        )

        result = await service.ingest_documents(file_paths=["manual.pdf"])

        self.assertEqual(result["status"], "warning")
        self.assertIn("already indexed", result["message"])
        self.assertEqual(result["chunks_created"], 0)
        self.assertEqual(service.vector_store.added_documents, [])
        self.assertFalse(service.vector_store.persist_called)

    async def test_same_batch_duplicate_is_indexed_once_and_reported(self) -> None:
        service = make_chat_service(
            documents=[
                make_document("same-hash", "C:\\uploads\\first\\manual.pdf", 1),
                make_document("same-hash", "C:\\uploads\\first\\manual.pdf", 2),
                make_document("same-hash", "C:\\uploads\\second\\manual.pdf", 1),
            ]
        )

        result = await service.ingest_documents(file_paths=["first.pdf", "second.pdf"])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["documents_processed"], 1)
        self.assertEqual(result["chunks_created"], 2)
        self.assertEqual(result["message"], "Skipped 1 duplicate PDF(s).")
        self.assertEqual(len(service.vector_store.added_documents), 2)
        self.assertTrue(service.vector_store.persist_called)

    async def test_load_failure_returns_error_without_writing_to_vector_store(self) -> None:
        service = make_chat_service(loader_error=RuntimeError("corrupt.pdf is invalid"))

        result = await service.ingest_documents(file_paths=["corrupt.pdf"])

        self.assertEqual(result["status"], "error")
        self.assertIn("corrupt.pdf is invalid", result["message"])
        self.assertEqual(service.vector_store.added_documents, [])
        self.assertFalse(service.vector_store.persist_called)

    async def test_vector_store_failure_is_not_reported_as_success(self) -> None:
        service = make_chat_service(
            documents=[make_document("new-hash", "C:\\uploads\\manual.pdf")],
            add_error=RuntimeError("write failed"),
        )

        result = await service.ingest_documents(file_paths=["manual.pdf"])

        self.assertEqual(result["status"], "error")
        self.assertIn("write failed", result["message"])
        self.assertIn("may be incomplete", result["message"])
        self.assertEqual(result["chunks_created"], 0)
        self.assertFalse(service.vector_store.persist_called)


class IngestionTimeoutTests(unittest.TestCase):
    def test_ingestion_api_uses_longer_timeout(self) -> None:
        with patch(
            "frontend.components.api_client.call_api",
            return_value={"error": "Request timed out"},
        ) as call_api:
            result = api_ingest_documents(file_paths=["manual.pdf"])

        self.assertIn("error", result)
        self.assertEqual(call_api.call_args.kwargs["timeout"], 300)

    def test_api_timeout_returns_large_request_diagnostic(self) -> None:
        with patch(
            "frontend.components.api_client.requests.post",
            side_effect=requests.exceptions.Timeout,
        ):
            result = call_api("/ingest", method="POST", data={}, timeout=300)

        self.assertIn("error", result)
        self.assertIn("large request", result["error"])

    def test_upload_helper_sends_uploaded_files_as_multipart_data(self) -> None:
        uploads = [
            SimpleNamespace(
                name="manual.pdf",
                size=8,
                getvalue=lambda: b"%PDF-1.7",
            ),
            SimpleNamespace(
                name="second.pdf",
                size=8,
                getvalue=lambda: b"%PDF-1.7",
            ),
        ]
        multipart_files = [
            ("files", ("manual.pdf", b"%PDF-1.7", "application/pdf")),
            ("files", ("second.pdf", b"%PDF-1.7", "application/pdf")),
        ]

        with patch(
            "frontend.components.api_client.call_api",
            return_value={"status": "success"},
        ) as api_call:
            result = api_upload_documents(uploads)

        self.assertEqual(result["status"], "success")
        api_call.assert_called_once_with(
            "/ingest/upload",
            "POST",
            files=multipart_files,
            timeout=300,
        )

    def test_upload_helper_rejects_oversized_batches_before_reading_contents(self) -> None:
        upload = SimpleNamespace(
            name="large.pdf",
            size=300 * 1024 * 1024,
            getvalue=lambda: self.fail("Oversized upload content must not be read."),
        )
        with patch("frontend.components.api_client.call_api") as api_call:
            result = api_upload_documents([upload])

        self.assertIn("200 MiB", result["error"])
        api_call.assert_not_called()

    def test_call_api_preserves_multipart_encoding_and_auth_headers(self) -> None:
        class FakeResponse:
            def raise_for_status(self):
                return None

            @staticmethod
            def json():
                return {"status": "success"}

        multipart_files = [
            ("files", ("manual.pdf", b"%PDF-1.7", "application/pdf"))
        ]
        headers = {"Authorization": "Bearer session-token"}
        with (
            patch(
                "frontend.components.api_client._request_headers",
                return_value=headers,
            ),
            patch(
                "frontend.components.api_client.requests.post",
                return_value=FakeResponse(),
            ) as post,
        ):
            result = call_api(
                "/ingest/upload",
                method="POST",
                files=multipart_files,
                timeout=300,
            )

        self.assertEqual(result["status"], "success")
        post.assert_called_once_with(
            f"{API_BASE_URL}/ingest/upload",
            files=multipart_files,
            headers=headers,
            timeout=300,
        )


class IngestionPageTests(unittest.TestCase):
    def test_page_refreshes_only_after_successful_ingestion(self) -> None:
        for result_status, expected_refreshes in (("warning", 0), ("success", 1)):
            app = AppTest.from_file(str(ENTRYPOINT), default_timeout=60)
            app.session_state["current_page"] = "knowledge_base"
            app.session_state["access_token"] = "test-admin-token"
            app.session_state["current_user"] = {
                "email": "admin@example.com",
                "role": "admin",
            }

            with (
                patch(
                    "frontend.pages.knowledge_base.ingest_documents",
                    return_value={
                        "status": result_status,
                        "documents_processed": 1,
                        "chunks_created": 2,
                    },
                ),
                patch(
                    "frontend.pages.knowledge_base.get_documents_list",
                    return_value={"documents": [], "total": 0},
                ),
                patch("frontend.pages.knowledge_base.set_ingestion_status"),
                patch("frontend.pages.knowledge_base.st.rerun") as rerun,
            ):
                app.run()
                app.button(key="kb_ingest_folder").click().run()

            self.assertFalse(app.exception)
            self.assertEqual(rerun.call_count, expected_refreshes)

    def test_selected_pdf_files_are_sent_through_authenticated_upload_client(self) -> None:
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
                return_value={"documents": [], "total": 0},
            ),
            patch(
                "frontend.pages.knowledge_base.upload_documents",
                return_value={
                    "status": "success",
                    "documents_processed": 1,
                    "chunks_created": 2,
                },
            ) as upload_documents,
            patch("frontend.pages.knowledge_base.set_ingestion_status"),
            patch("frontend.pages.knowledge_base.st.rerun"),
        ):
            app.run()
            app.file_uploader(key="kb_file_uploader").upload(
                "manual.pdf",
                b"%PDF-1.7",
            ).run()
            app.button(key="kb_ingest_upload").click().run()

        self.assertFalse(app.exception)
        upload_documents.assert_called_once()
        uploaded_file = upload_documents.call_args.args[0][0]
        self.assertEqual(uploaded_file.name, "manual.pdf")


if __name__ == "__main__":
    unittest.main()
