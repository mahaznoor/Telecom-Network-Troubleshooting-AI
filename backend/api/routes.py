"""API routes for the Telecom Network Troubleshooting AI."""

import logging
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from backend.api.auth import (
    AuthUser,
    auth_router,
    get_current_user,
    require_admin,
)
from backend.services.chat_service import ChatService
from backend.services.session_store import (
    FeedbackTargetNotFoundError,
    SessionOwnershipError,
)
from backend.config import (
    MAX_DOCUMENT_UPLOAD_BYTES,
    MAX_DOCUMENT_UPLOAD_FILES,
    settings,
)


router = APIRouter()
logger = logging.getLogger(__name__)
router.include_router(auth_router)
_MAX_UPLOAD_BYTES = MAX_DOCUMENT_UPLOAD_BYTES
_MAX_UPLOAD_FILES = MAX_DOCUMENT_UPLOAD_FILES


# Singleton ChatService instance
_chat_service_instance: Optional[ChatService] = None


def _is_within_data_dir(path: str) -> bool:
    """Resolve an ingestion path and ensure it remains under DATA_DIR."""
    try:
        root = Path(settings.DATA_DIR).resolve()
        candidate = Path(path).resolve(strict=True)
        candidate.relative_to(root)
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def get_chat_service() -> ChatService:
    """Dependency to get singleton chat service instance."""
    global _chat_service_instance
    if _chat_service_instance is None:
        _chat_service_instance = ChatService()
    return _chat_service_instance


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., description="User's troubleshooting question", min_length=1, max_length=4000)
    session_id: Optional[str] = Field(None, description="Optional session ID for conversation history")
    top_k: Optional[int] = Field(settings.TOP_K, ge=1, le=20, description="Number of relevant documents to retrieve")
    similarity_threshold: Optional[float] = Field(None, ge=0.0, le=1.0, description="Minimum similarity score for retrieved documents")
    use_mmr: Optional[bool] = Field(None, description="Enable diverse result retrieval using MMR")


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""
    answer: str = Field(..., description="AI-generated troubleshooting answer")
    sources: List[Dict[str, Any]] = Field(default_factory=list, description="Source documents used")
    session_id: str = Field(..., description="Session ID for conversation tracking")
    message_id: Optional[int] = Field(None, description="Persisted assistant message ID")
    error: Optional[str] = Field(None, description="Error code if any")


class IngestRequest(BaseModel):
    """Request model for document ingestion."""
    model_config = ConfigDict(extra="forbid")

    file_paths: Optional[List[str]] = Field(
        None,
        max_length=100,
        description="Specific file paths to ingest",
    )
    directory: Optional[str] = Field(
        None,
        max_length=1024,
        description="Directory to ingest all PDFs from (default: data/manuals/)",
    )

    @field_validator("file_paths")
    @classmethod
    def validate_file_paths(cls, paths: Optional[List[str]]) -> Optional[List[str]]:
        if paths is not None and any(
            not path.strip() or len(path) > 1024
            for path in paths
        ):
            raise ValueError("Invalid ingestion file path.")
        return paths

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, directory: Optional[str]) -> Optional[str]:
        if directory is not None and not directory.strip():
            raise ValueError("Invalid ingestion directory.")
        return directory

    @model_validator(mode="after")
    def validate_single_source(self) -> "IngestRequest":
        if self.file_paths is not None and self.directory is not None:
            raise ValueError("Choose file_paths or directory, not both.")
        return self


class IngestResponse(BaseModel):
    """Response model for document ingestion."""
    status: str = Field(..., description="Ingestion status")
    documents_processed: int = Field(..., description="Number of documents processed")
    chunks_created: int = Field(..., description="Number of text chunks created")
    message: Optional[str] = Field(None, description="Additional message")
    completed_at: Optional[str] = Field(None, description="UTC completion timestamp")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = Field(..., description="Overall health status")
    version: str = Field(..., description="API version")
    groq_configured: bool = Field(..., description="Whether Groq API key is configured")
    groq_client_initialized: bool = Field(..., description="Whether the Groq client initialized")
    vector_db_available: bool = Field(..., description="Whether the vector database can be queried")
    vector_db_ready: bool = Field(..., description="Whether vector database has documents")
    document_count: int = Field(..., description="Number of documents in vector store")
    embedding_model: str = Field(..., description="Embedding model in use")
    llm_model: str = Field(..., description="LLM model in use")


class SessionClearResponse(BaseModel):
    """Response for session clear."""
    status: str
    message: str


class ChatSessionSummary(BaseModel):
    session_id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class ChatSessionsResponse(BaseModel):
    sessions: List[ChatSessionSummary] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: str
    content: str
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str
    message_id: Optional[int] = Field(None, description="Persisted message ID")


class ChatSessionDetail(BaseModel):
    session_id: str
    title: str
    created_at: str
    updated_at: str
    messages: List[ChatMessage] = Field(default_factory=list)


class SearchRequest(BaseModel):
    """Request model for document search endpoint."""
    q: str = Field(..., description="Search query text", min_length=1)
    top_k: Optional[int] = Field(settings.TOP_K, ge=1, le=20, description="Number of results to return")
    similarity_threshold: Optional[float] = Field(None, ge=0.0, le=1.0, description="Minimum similarity score")
    use_mmr: Optional[bool] = Field(True, description="Enable diverse result retrieval using MMR")


class SearchResult(BaseModel):
    """Single search result document."""
    source_file: str = Field(..., description="Source PDF filename")
    page_number: int = Field(..., description="Page number in source document")
    content_preview: str = Field(..., description="Text snippet from the document")
    similarity_score: Optional[float] = Field(None, description="Similarity score (lower=better for distance)")


class SearchResponse(BaseModel):
    """Response model for document search endpoint."""
    results: List[SearchResult] = Field(default_factory=list, description="Search results")
    total: int = Field(..., description="Total number of results returned")
    query: str = Field(..., description="Original search query")


class DocumentInfo(BaseModel):
    """Information about a single indexed document."""
    filename: str = Field(..., description="PDF filename")
    document_id: Optional[str] = Field(
        None,
        description="Content fingerprint used for administrator index removal",
    )
    total_pages: int = Field(..., description="Total pages in the document")
    chunk_count: int = Field(..., description="Number of text chunks indexed")
    page_numbers: List[int] = Field(default_factory=list, description="Pages that have indexed content")
    file_size_bytes: Optional[int] = Field(None, description="Original file size in bytes, when recorded")
    ingested_at: Optional[str] = Field(None, description="Ingestion timestamp, when recorded")


class DocumentsResponse(BaseModel):
    """Response model for documents listing endpoint."""
    documents: List[DocumentInfo] = Field(default_factory=list, description="List of indexed documents")
    total: int = Field(..., description="Total number of unique documents")

class DocumentDeleteResponse(BaseModel):
    status: str
    message: str
    chunks_deleted: int


class VectorDBDeleteRequest(BaseModel):
    """Request model for vector database deletion."""
    model_config = ConfigDict(extra="forbid")

    confirm: bool = Field(False, description="Must be true to confirm deletion")


class VectorDBDeleteResponse(BaseModel):
    """Response model for vector database deletion."""
    status: str = Field(..., description="Deletion status")
    message: str = Field(..., description="Status message")


class FeedbackRequest(BaseModel):
    """Request model for rating an assistant response."""
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1, max_length=64)
    message_id: StrictInt = Field(..., gt=0)
    rating: Literal["helpful", "not_helpful"]
    comment: Optional[str] = Field(None, max_length=1000)


class FeedbackRecord(BaseModel):
    """Stored rating and optional comment for one assistant response."""
    feedback_id: int
    session_id: str
    message_id: int
    rating: Literal["helpful", "not_helpful"]
    comment: Optional[str] = None
    created_at: str
    updated_at: str


class FeedbackSubmissionResponse(FeedbackRecord):
    pass


class FeedbackSummaryResponse(BaseModel):
    total_feedback: int
    helpful_count: int
    not_helpful_count: int
    positive_percentage: float
    recent_feedback: List[FeedbackRecord] = Field(default_factory=list)


@router.get("/health", response_model=HealthResponse)
async def health_check(
    chat_service: ChatService = Depends(get_chat_service)
):
    """Health check endpoint with system status."""
    stats = chat_service.get_collection_stats()
    vector_db_available = "error" not in stats
    document_count = stats.get("document_count", 0) if vector_db_available else 0
    groq_client_initialized = chat_service.is_ready()

    return HealthResponse(
        status="healthy" if vector_db_available and groq_client_initialized else "degraded",
        version="1.0.0",
        groq_configured=bool(settings.GROQ_API_KEY),
        groq_client_initialized=groq_client_initialized,
        vector_db_available=vector_db_available,
        vector_db_ready=vector_db_available and document_count > 0,
        document_count=document_count,
        embedding_model=stats.get("embedding_model", "unknown"),
        llm_model=settings.LLM_MODEL,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    current_user: AuthUser = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service)
):
    """Process a troubleshooting question and return an AI-generated answer."""
    try:
        result = await chat_service.process_question(
            question=request.question,
            user_id=current_user.user_id,
            session_id=request.session_id,
            top_k=request.top_k,
            similarity_threshold=request.similarity_threshold,
            use_mmr=request.use_mmr,
        )
        if result.get("error"):
            result["answer"] = "Chat processing failed. Please try again."
            result["sources"] = []
        return ChatResponse(**result)
    except SessionOwnershipError:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    except Exception as error:
        logger.error("Chat request failed (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail="Chat processing failed.")


@router.get("/chat/sessions", response_model=ChatSessionsResponse)
async def list_chat_sessions(
    current_user: AuthUser = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service)
):
    """List saved chat conversations."""
    try:
        return ChatSessionsResponse(
            sessions=chat_service.list_chat_sessions(current_user.user_id)
        )
    except Exception as error:
        logger.error("Could not list chat sessions (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail="Saved conversations are unavailable.")


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionDetail)
async def get_chat_session(
    session_id: str,
    current_user: AuthUser = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service)
):
    """Load one saved conversation."""
    try:
        session = chat_service.get_chat_session(session_id, current_user.user_id)
    except Exception as error:
        logger.error("Could not load chat session (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail="Conversation could not be loaded.")
    if session is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return ChatSessionDetail(**session)


@router.post("/feedback", response_model=FeedbackSubmissionResponse)
async def submit_response_feedback(
    request: FeedbackRequest,
    current_user: AuthUser = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    """Create or update feedback for a persisted assistant response."""
    comment = request.comment.strip() if request.comment is not None else None
    try:
        result = chat_service.submit_feedback(
            session_id=request.session_id,
            user_id=current_user.user_id,
            message_id=request.message_id,
            rating=request.rating,
            comment=comment or None,
        )
    except FeedbackTargetNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Conversation or assistant response not found.",
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid feedback data.")
    except sqlite3.Error as error:
        logger.error(
            "Could not save response feedback (%s)",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Feedback could not be saved. Please try again.",
        )
    return FeedbackSubmissionResponse(**result)


@router.get("/feedback/summary", response_model=FeedbackSummaryResponse)
async def get_response_feedback_summary(
    limit: int = Query(20, ge=1, le=100),
    rating: Optional[Literal["helpful", "not_helpful"]] = Query(None),
    current_user: AuthUser = Depends(require_admin),
    chat_service: ChatService = Depends(get_chat_service),
):
    """Return bounded aggregate feedback and recent records for the review page."""
    try:
        result = chat_service.get_feedback_summary(limit=limit, rating=rating)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid feedback summary filter.")
    except sqlite3.Error as error:
        logger.error(
            "Could not load response feedback summary (%s)",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Feedback summary is temporarily unavailable.",
        )
    return FeedbackSummaryResponse(**result)


@router.post("/ingest", response_model=IngestResponse)
async def ingest_documents(
    request: IngestRequest,
    current_user: AuthUser = Depends(require_admin),
    chat_service: ChatService = Depends(get_chat_service)
):
    """Ingest PDF documents into the vector store."""
    try:
        if request.file_paths is not None:
            if any(
                Path(file_path).suffix.lower() != ".pdf"
                or not _is_within_data_dir(file_path)
                or not Path(file_path).is_file()
                for file_path in request.file_paths
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Ingestion files must be PDFs inside DATA_DIR.",
                )
            directory = settings.DATA_DIR
        else:
            directory = request.directory or settings.DATA_DIR
            if (
                not _is_within_data_dir(directory)
                or not Path(directory).is_dir()
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Ingestion directory must be inside DATA_DIR.",
                )
        
        result = await chat_service.ingest_documents(
            file_paths=request.file_paths,
            directory=directory
        )
        if result.get("status") == "error":
            result["message"] = "Document ingestion failed. Check backend logs."
        return IngestResponse(**result)
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Document ingestion request failed (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail="Document ingestion failed.")


@router.post("/ingest/upload", response_model=IngestResponse)
async def upload_and_ingest_documents(
    files: List[UploadFile] = File(...),
    current_user: AuthUser = Depends(require_admin),
    chat_service: ChatService = Depends(get_chat_service),
):
    """Persist and ingest administrator-uploaded PDFs without sharing paths."""
    if not files:
        raise HTTPException(status_code=422, detail="Select at least one PDF.")
    if len(files) > _MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload no more than {_MAX_UPLOAD_FILES} files at once.",
        )

    filenames = []
    for uploaded_file in files:
        supplied_name = uploaded_file.filename or ""
        filename = supplied_name.replace("\\", "/").rsplit("/", 1)[-1]
        if (
            not filename
            or filename in {".", ".."}
            or Path(filename).suffix.lower() != ".pdf"
        ):
            raise HTTPException(
                status_code=422,
                detail="Every uploaded document must be a PDF.",
            )
        filenames.append(filename)

    upload_directory = (
        Path(settings.DATA_DIR).resolve()
        / "uploads"
        / uuid.uuid4().hex
    )
    saved_paths = []
    total_bytes = 0
    try:
        upload_directory.mkdir(parents=True, exist_ok=False)
        for index, (uploaded_file, filename) in enumerate(
            zip(files, filenames)
        ):
            file_directory = upload_directory / str(index)
            file_directory.mkdir()
            file_path = file_directory / filename
            with file_path.open("wb") as target:
                while True:
                    chunk = await uploaded_file.read(1024 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > _MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail="The upload batch must be 200 MiB or smaller.",
                        )
                    target.write(chunk)
            if total_bytes == 0:
                raise HTTPException(
                    status_code=422,
                    detail="Uploaded PDFs cannot be empty.",
                )
            saved_paths.append(str(file_path))
    except Exception:
        try:
            shutil.rmtree(upload_directory)
        except OSError as cleanup_error:
            logger.error(
                "Incomplete upload cleanup failed (%s)",
                type(cleanup_error).__name__,
            )
        raise
    finally:
        for uploaded_file in files:
            await uploaded_file.close()

    try:
        result = await chat_service.ingest_documents(file_paths=saved_paths)
        if result.get("status") == "error":
            result["message"] = (
                "Document ingestion failed. The uploaded PDFs remain in "
                "DATA_DIR\\uploads for review or retry."
            )
        return IngestResponse(**result)
    except Exception as error:
        logger.error("Uploaded document ingestion failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=500,
            detail="Uploaded documents could not be ingested.",
        )


@router.delete("/chat/{session_id}", response_model=SessionClearResponse)
async def clear_session(
    session_id: str,
    current_user: AuthUser = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service)
):
    """Clear conversation history for a session."""
    try:
        deleted = await chat_service.clear_session(session_id, current_user.user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return SessionClearResponse(
            status="success",
            message="Conversation cleared."
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Could not clear chat session (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail="Conversation could not be cleared.")


@router.delete("/chat", response_model=SessionClearResponse)
async def clear_all_sessions(
    current_user: AuthUser = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service)
):
    """Clear only conversations belonging to the authenticated user."""
    try:
        chat_service.clear_all_sessions(current_user.user_id)
        return SessionClearResponse(
            status="success",
            message="Your conversations were cleared."
        )
    except Exception as error:
        logger.error("Could not clear chat sessions (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail="Conversations could not be cleared.")


@router.get("/search", response_model=SearchResponse)
async def search_documents(
    q: str = Query(..., min_length=1, max_length=1000),
    top_k: Optional[int] = Query(None, ge=1, le=20),
    similarity_threshold: Optional[float] = Query(None, ge=0.0, le=1.0),
    use_mmr: Optional[bool] = Query(None),
    current_user: AuthUser = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service)
):
    """Search indexed documents directly via ChromaDB (no Groq/LLM involved)."""
    try:
        result = await chat_service.search_documents(
            query=q,
            top_k=top_k,
            similarity_threshold=similarity_threshold,
            use_mmr=use_mmr,
        )
        return SearchResponse(**result)
    except Exception as error:
        logger.error("Document search failed (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail="Document search failed.")


@router.get("/documents", response_model=DocumentsResponse)
async def list_documents(
    current_user: AuthUser = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service)
):
    """List all unique indexed PDF documents with metadata from ChromaDB."""
    try:
        result = await chat_service.get_documents_list()
        return DocumentsResponse(**result)
    except Exception as error:
        logger.error("Document listing failed (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail="Documents are unavailable.")

@router.delete(
    "/documents/{document_id}",
    response_model=DocumentDeleteResponse,
)
async def delete_document(
    document_id: str,
    current_user: AuthUser = Depends(require_admin),
    chat_service: ChatService = Depends(get_chat_service),
):
    """Remove one document's indexed chunks without deleting its source PDF."""
    try:
        chunks_deleted = chat_service.delete_document(document_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid document identifier.")
    except Exception as error:
        logger.error("Indexed document deletion failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=500,
            detail="Indexed document could not be removed.",
        )
    if chunks_deleted == 0:
        raise HTTPException(status_code=404, detail="Indexed document not found.")
    return DocumentDeleteResponse(
        status="success",
        message=(
            "The document was removed from the index. Its source PDF remains "
            "in server storage."
        ),
        chunks_deleted=chunks_deleted,
    )


@router.delete("/vectordb", response_model=VectorDBDeleteResponse)
async def delete_vector_database(
    request: VectorDBDeleteRequest,
    current_user: AuthUser = Depends(require_admin),
    chat_service: ChatService = Depends(get_chat_service)
):
    """Delete the entire vector database collection.

    Requires confirm=true to prevent accidental deletion.
    Does NOT delete PDF manuals from data/manuals/.
    """
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Deletion not confirmed. Set confirm=true to delete the vector database."
        )
    try:
        result = await chat_service.delete_vector_database(confirm=True)
        if result.get("status") != "success":
            logger.error("Vector database deletion did not complete")
            raise HTTPException(
                status_code=500,
                detail="Vector database operation failed.",
            )
        return VectorDBDeleteResponse(**result)
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Vector database deletion failed (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail="Vector database operation failed.")