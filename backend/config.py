"""Configuration settings for the Telecom Network Troubleshooting AI."""

import os
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    # API Settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: List[str] = ["http://localhost:8501", "http://127.0.0.1:8501"]

    # Groq LLM Settings
    GROQ_API_KEY: str = ""
    LLM_MODEL: str = "llama-3.1-70b-versatile"  # GPT-OSS 120B not yet on Groq; use best available
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 4096

    # HuggingFace BGE Embedding Settings
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIMENSION: int = 384
    EMBEDDING_DEVICE: str = "cpu"  # or "cuda" if GPU available

    # Vector Store Settings
    CHROMA_PERSIST_DIR: str = "./storage/chroma"
    CHROMA_COLLECTION_NAME: str = "telecom_manuals"

    # Ingestion Settings
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200
    DATA_DIR: str = "./data/manuals"

    # Retrieval Settings
    TOP_K: int = 5
    SIMILARITY_THRESHOLD: float = 0.7

    # Evaluation Settings
    EVAL_QUESTIONS_FILE: str = "./evaluation/test_questions.json"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()