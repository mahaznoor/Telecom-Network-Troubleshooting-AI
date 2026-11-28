"""Configuration settings for the Telecom Network Troubleshooting AI."""

import os
from typing import List, Literal
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

MAX_DOCUMENT_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_DOCUMENT_UPLOAD_FILES = 100


class Settings(BaseSettings):
    """Application settings."""

    # API Settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: List[str] = ["http://localhost:8501", "http://127.0.0.1:8501"]
    API_BASE_URL: str = "http://localhost:8000/api/v1"
    ENVIRONMENT: Literal["development", "production"] = "development"
    JWT_SECRET_KEY: str = ""
    AUDIT_IDENTIFIER_HMAC_SECRET: SecretStr = SecretStr("")
    JWT_ACCESS_TOKEN_MINUTES: int = Field(default=60, ge=1, le=1440)
    BCRYPT_ROUNDS: int = Field(default=12, ge=10, le=14)
    LOGIN_IP_MAX_FAILURES: int = Field(default=20, ge=1, le=1000)
    LOGIN_ACCOUNT_MAX_FAILURES: int = Field(default=5, ge=1, le=100)
    LOGIN_FAILURE_WINDOW_SECONDS: int = Field(default=900, ge=1, le=86400)
    LOGIN_ACCOUNT_LOCKOUT_SECONDS: int = Field(default=900, ge=1, le=86400)
    VERIFICATION_EMAIL_IP_MAX_PER_HOUR: int = Field(default=10, ge=1, le=1000)
    VERIFICATION_EMAIL_ADDRESS_MAX_PER_HOUR: int = Field(default=3, ge=1, le=100)
    EMAIL_VERIFICATION_MINUTES: int = Field(default=20, ge=15, le=30)
    PASSWORD_RESET_MINUTES: int = Field(default=30, ge=15, le=60)
    SMTP_HOST: str = ""
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: SecretStr = SecretStr("")
    SMTP_FROM_EMAIL: str = ""
    SMTP_USE_TLS: bool = True
    FRONTEND_BASE_URL: str = ""

    @field_validator("AUDIT_IDENTIFIER_HMAC_SECRET")
    @classmethod
    def validate_audit_identifier_hmac_secret(
        cls,
        value: SecretStr,
    ) -> SecretStr:
        secret = value.get_secret_value()
        if secret and len(secret.encode("utf-8")) < 32:
            raise ValueError(
                "AUDIT_IDENTIFIER_HMAC_SECRET must contain at least 32 bytes."
            )
        return value

    # Groq LLM Settings
    GROQ_API_KEY: str = ""
    LLM_MODEL: str = "openai/gpt-oss-120b"
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