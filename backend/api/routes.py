"""API routes for the Telecom Network Troubleshooting AI."""

from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from backend.services.chat_service import ChatService
from backend.config import settings


router = APIRouter()


# Singleton ChatService instance
_chat_service_instance: Optional[ChatService] = None


def get_chat_service() -> ChatService:
    """Dependency to get singleton chat service instance."""
    global _chat_service_instance
    if _chat_service_instance is None:
        _chat_service_instance = ChatService()
    return _chat_service_instance


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""
    question: str = Field(..., description="User's troubleshooting question", min_length=1)
    session_id: Optional[str] = Field(None, description="Optional session ID for conversation history")
    top_k: Optional[int] = Field(settings.TOP_K, ge=1, le=20, description="Number of relevant documents to retrieve")
    similarity_threshold: Optional[float] = Field(None, ge=0.0, le=1.0, description="Minimum similarity score for retrieved documents")
    use_mmr: Optional[bool] = Field(None, description="Enable diverse result retrieval using MMR")


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""
    answer: str = Field(..., description="AI-generated troubleshooting answer")
    sources: List[Dict[str, Any]] = Field(default_factory=list, description="Source documents used")
    session_id: str = Field(..., description="Session ID for conversation tracking")
    error: Optional[str] = Field(None, description="Error code if any")


class IngestRequest(BaseModel):
    """Request model for document ingestion."""
    file_paths: Optional[List[str]] = Field(None, description="Specific file paths to ingest")
    directory: Optional[str] = Field(None, description="Directory to ingest all PDFs from (default: data/manuals/)")


class IngestResponse(BaseModel):
    """Response model for document ingestion."""
    status: str = Field(..., description="Ingestion status")
    documents_processed: int = Field(..., description="Number of documents processed")
    chunks_created: int = Field(..., description="Number of text chunks created")
    message: Optional[str] = Field(None, description="Additional message")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = Field(..., description="Overall health status")
    version: str = Field(..., description="API version")
    groq_configured: bool = Field(..., description="Whether Groq API key is configured")
    vector_db_ready: bool = Field(..., description="Whether vector database has documents")
    document_count: int = Field(..., description="Number of documents in vector store")
    embedding_model: str = Field(..., description="Embedding model in use")
    llm_model: str = Field(..., description="LLM model in use")


class SessionClearResponse(BaseModel):
    """Response for session clear."""
    status: str
    message: str


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
    total_pages: int = Field(..., description="Total pages in the document")
    chunk_count: int = Field(..., description="Number of text chunks indexed")
    page_numbers: List[int] = Field(default_factory=list, description="Pages that have indexed content")


class DocumentsResponse(BaseModel):
    """Response model for documents listing endpoint."""
    documents: List[DocumentInfo] = Field(default_factory=list, description="List of indexed documents")
    total: int = Field(..., description="Total number of unique documents")


class VectorDBDeleteRequest(BaseModel):
    """Request model for vector database deletion."""
    confirm: bool = Field(False, description="Must be true to confirm deletion")


class VectorDBDeleteResponse(BaseModel):
    """Response model for vector database deletion."""
    status: str = Field(..., description="Deletion status")
    message: str = Field(..., description="Status message")


@router.get("/health", response_model=HealthResponse)
async def health_check(
    chat_service: ChatService = Depends(get_chat_service)
):
    """Health check endpoint with system status."""
    stats = chat_service.get_collection_stats()
    
    return HealthResponse(
        status="healthy" if chat_service.is_ready() else "degraded",
        version="1.0.0",
        groq_configured=chat_service.is_ready(),
        vector_db_ready=stats.get("document_count", 0) > 0,
        document_count=stats.get("document_count", 0),
        embedding_model=stats.get("embedding_model", "unknown"),
        llm_model=settings.LLM_MODEL,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    chat_service: ChatService = Depends(get_chat_service)
):
    """Process a troubleshooting question and return an AI-generated answer."""
    try:
        result = await chat_service.process_question(
            question=request.question,
            session_id=request.session_id,
            top_k=request.top_k,
            similarity_threshold=request.similarity_threshold,
            use_mmr=request.use_mmr,
        )
        return ChatResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat processing failed: {str(e)}")


@router.post("/ingest", response_model=IngestResponse)
async def ingest_documents(
    request: IngestRequest,
    chat_service: ChatService = Depends(get_chat_service)
):
    """Ingest PDF documents into the vector store."""
    try:
        # Use default data directory if not specified
        directory = request.directory or settings.DATA_DIR
        
        result = await chat_service.ingest_documents(
            file_paths=request.file_paths,
            directory=directory
        )
        return IngestResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Document ingestion failed: {str(e)}")


@router.delete("/chat/{session_id}", response_model=SessionClearResponse)
async def clear_session(
    session_id: str,
    chat_service: ChatService = Depends(get_chat_service)
):
    """Clear conversation history for a session."""
    try:
        await chat_service.clear_session(session_id)
        return SessionClearResponse(
            status="success",
            message=f"Session {session_id} cleared"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear session: {str(e)}")


@router.delete("/chat", response_model=SessionClearResponse)
async def clear_all_sessions(
    chat_service: ChatService = Depends(get_chat_service)
):
    """Clear all conversation sessions."""
    try:
        chat_service.clear_all_sessions()
        return SessionClearResponse(
            status="success",
            message="All sessions cleared"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear sessions: {str(e)}")


@router.get("/search", response_model=SearchResponse)
async def search_documents(
    q: str,
    top_k: Optional[int] = None,
    similarity_threshold: Optional[float] = None,
    use_mmr: Optional[bool] = None,
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Document search failed: {str(e)}")


@router.get("/documents", response_model=DocumentsResponse)
async def list_documents(
    chat_service: ChatService = Depends(get_chat_service)
):
    """List all unique indexed PDF documents with metadata from ChromaDB."""
    try:
        result = await chat_service.get_documents_list()
        return DocumentsResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")


@router.delete("/vectordb", response_model=VectorDBDeleteResponse)
async def delete_vector_database(
    request: VectorDBDeleteRequest,
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
        return VectorDBDeleteResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete vector database: {str(e)}")