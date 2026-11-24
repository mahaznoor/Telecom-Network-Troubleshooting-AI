"""API routes for the Telecom Network Troubleshooting AI."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from backend.services.chat_service import ChatService
from backend.config import settings


router = APIRouter()


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""
    question: str = Field(..., description="User's troubleshooting question")
    session_id: Optional[str] = Field(None, description="Optional session ID for conversation history")
    top_k: Optional[int] = Field(settings.TOP_K, description="Number of relevant documents to retrieve")


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""
    answer: str = Field(..., description="AI-generated troubleshooting answer")
    sources: List[Dict[str, Any]] = Field(default_factory=list, description="Source documents used")
    session_id: str = Field(..., description="Session ID for conversation tracking")


class IngestRequest(BaseModel):
    """Request model for document ingestion."""
    file_paths: Optional[List[str]] = Field(None, description="Specific file paths to ingest")
    directory: Optional[str] = Field(None, description="Directory to ingest all PDFs from")


class IngestResponse(BaseModel):
    """Response model for document ingestion."""
    status: str = Field(..., description="Ingestion status")
    documents_processed: int = Field(..., description="Number of documents processed")
    chunks_created: int = Field(..., description="Number of text chunks created")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    version: str = "1.0.0"


def get_chat_service() -> ChatService:
    """Dependency to get chat service instance."""
    return ChatService()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse()


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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest", response_model=IngestResponse)
async def ingest_documents(
    request: IngestRequest,
    chat_service: ChatService = Depends(get_chat_service)
):
    """Ingest PDF documents into the vector store."""
    try:
        result = await chat_service.ingest_documents(
            file_paths=request.file_paths,
            directory=request.directory
        )
        return IngestResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
async def clear_session(
    session_id: str,
    chat_service: ChatService = Depends(get_chat_service)
):
    """Clear conversation history for a session."""
    try:
        await chat_service.clear_session(session_id)
        return {"status": "success", "message": f"Session {session_id} cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))