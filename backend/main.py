"""Main entry point for the Telecom Network Troubleshooting AI backend."""

from contextlib import asynccontextmanager
from ipaddress import ip_address
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.api.routes import router as api_router
from backend.config import settings


def validate_cors_configuration(environment: str, origins: list[str]) -> None:
    """Reject wildcard CORS and require trusted HTTPS origins in production."""
    if not origins or any("*" in origin for origin in origins):
        raise ValueError("CORS_ORIGINS must contain explicit trusted origins.")
    if environment != "production":
        return

    for origin in origins:
        parsed = urlsplit(origin)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        is_loopback = hostname == "localhost" or hostname.endswith(".localhost")
        if hostname:
            try:
                address = ip_address(hostname)
                is_loopback = (
                    is_loopback
                    or address.is_loopback
                    or address.is_unspecified
                    or (
                        address.ipv4_mapped is not None
                        and (
                            address.ipv4_mapped.is_loopback
                            or address.ipv4_mapped.is_unspecified
                        )
                    )
                )
            except ValueError:
                pass
        try:
            parsed.port
        except ValueError as error:
            raise ValueError(
                "Production CORS origins must be explicit HTTPS origins."
            ) from error
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or is_loopback
        ):
            raise ValueError(
                "Production CORS origins must be explicit HTTPS origins."
            )


def validate_deployment_configuration(
    environment: str,
    origins: list[str],
    jwt_secret_key: str,
    audit_identifier_hmac_secret: str = "",
) -> None:
    """Validate security-critical settings before serving production traffic."""
    validate_cors_configuration(environment, origins)
    if environment == "production" and len(jwt_secret_key.encode("utf-8")) < 32:
        raise ValueError("Production requires a JWT_SECRET_KEY of at least 32 bytes.")
    if environment == "production" and len(
        audit_identifier_hmac_secret.encode("utf-8")
    ) < 32:
        raise ValueError(
            "Production requires an AUDIT_IDENTIFIER_HMAC_SECRET "
            "of at least 32 bytes."
        )
    if (
        environment == "production"
        and audit_identifier_hmac_secret == jwt_secret_key
    ):
        raise ValueError(
            "Production requires a dedicated audit HMAC secret distinct "
            "from JWT_SECRET_KEY."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    print(f"Starting {settings.LLM_MODEL} on {settings.HOST}:{settings.PORT}")
    print(f"Vector store: {settings.CHROMA_PERSIST_DIR}")
    yield
    # Shutdown
    print("Shutting down...")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    validate_deployment_configuration(
        settings.ENVIRONMENT,
        settings.CORS_ORIGINS,
        settings.JWT_SECRET_KEY,
        settings.AUDIT_IDENTIFIER_HMAC_SECRET.get_secret_value(),
    )
    app = FastAPI(
        title="Telecom Network Troubleshooting AI",
        description="AI-powered troubleshooting for telecom network issues",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(api_router, prefix="/api/v1")

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": "Request validation failed."},
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)