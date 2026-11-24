"""Embeddings module using HuggingFace BGE models."""

from langchain_huggingface import HuggingFaceEmbeddings
from backend.config import settings


def get_embeddings_model() -> HuggingFaceEmbeddings:
    """Get configured HuggingFace BGE embeddings model.

    Returns:
        HuggingFaceEmbeddings instance configured for BGE model.
    """
    model_kwargs = {"device": settings.EMBEDDING_DEVICE}
    encode_kwargs = {"normalize_embeddings": True}

    return HuggingFaceEmbeddings(
        model_name=settings.EMBEDDING_MODEL,
        model_kwargs=model_kwargs,
        encode_kwargs=encode_kwargs,
    )