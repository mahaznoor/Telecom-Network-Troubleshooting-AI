"""Formatting and filtering helpers for indexed document metadata."""

from datetime import datetime
from typing import Dict, List, Optional


def format_file_size(size_bytes: Optional[int]) -> str:
    """Format file size in human-readable units."""
    if size_bytes is None:
        return "Unknown"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def format_ingested_at(timestamp: Optional[str]) -> str:
    """Format an ingestion timestamp, preserving unknown legacy values."""
    if not timestamp:
        return "Unknown"
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        formatted = parsed.strftime("%Y-%m-%d %H:%M:%S")
        timezone = parsed.strftime("%z")
        return f"{formatted} {timezone}" if timezone else formatted
    except (AttributeError, ValueError):
        return str(timestamp)


def filter_documents(documents: List[Dict], query: str) -> List[Dict]:
    """Filter indexed documents by filename."""
    normalized_query = query.strip().casefold()
    if not normalized_query:
        return documents
    return [
        document
        for document in documents
        if normalized_query in str(document.get("filename") or "").casefold()
    ]


def build_document_rows(documents: List[Dict]) -> List[Dict[str, object]]:
    """Convert API document metadata into user-facing table rows."""
    rows = []
    for document in documents:
        total_pages = document.get("total_pages")
        chunk_count = document.get("chunk_count")
        rows.append({
            "File name": document.get("filename") or "Unknown",
            "File size": format_file_size(document.get("file_size_bytes")),
            "Pages": str(total_pages) if isinstance(total_pages, int) and total_pages > 0 else "Unknown",
            "Indexed chunks": str(chunk_count) if isinstance(chunk_count, int) else "Unknown",
            "Ingested": format_ingested_at(document.get("ingested_at")),
        })
    return rows
