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
            top_k=request.top_k
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