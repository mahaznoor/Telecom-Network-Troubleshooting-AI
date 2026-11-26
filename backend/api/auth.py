"""Authentication endpoints and reusable authorization dependencies."""

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.config import settings
from backend.services.auth_service import (
    AuthConfigurationError,
    AuthenticationService,
    EmailAlreadyInUseError,
    EmailAlreadyRegisteredError,
    EmailNotVerifiedError,
    InvalidCurrentPasswordError,
    InvalidCredentialsError,
    PasswordReuseError,
    normalize_email,
    validate_password,
    validate_new_password,
    validate_name,
)
from backend.services.session_store import ChatSessionStore
from backend.services.email_service import (
    EmailDeliveryError,
    EmailService,
    SMTPConfigurationError,
)
from backend.services.login_limiter import (
    LoginRateLimitExceeded,
    LoginRateLimiter,
    get_login_rate_limiter,
)
from backend.services.verification_limiter import (
    VerificationEmailRateLimiter,
    get_verification_email_limiter,
)


auth_router = APIRouter()
logger = logging.getLogger(__name__)
_bearer_scheme = HTTPBearer(auto_error=False)
_auth_service_instance: Optional[AuthenticationService] = None


class AuthUser(BaseModel):
    """Public identity fields returned after authentication."""

    user_id: str
    name: str = ""
    email: str
    role: str
    created_at: str
    email_verified: bool = False
    session_id: Optional[str] = Field(default=None, exclude=True)


class EmailPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=12, max_length=72)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, value: str) -> str:
        validate_password(value)
        return value


class RegistrationRequest(EmailPasswordRequest):
    name: str = Field(..., min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return validate_name(value)


class LoginRequest(EmailPasswordRequest):
    pass


class VerifyEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=1, max_length=256)


class ResendVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class PasswordResetCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=12, max_length=72)

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, value: str) -> str:
        validate_new_password(value)
        return value


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(..., min_length=12, max_length=72)
    new_password: str = Field(..., min_length=12, max_length=72)
    revoke_other_sessions: bool = False

    @field_validator("current_password")
    @classmethod
    def validate_current_password(cls, value: str) -> str:
        validate_password(value)
        return value

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, value: str) -> str:
        validate_new_password(value)
        return value


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return validate_name(value)


class EmailChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(..., min_length=12, max_length=72)
    new_email: str = Field(..., min_length=3, max_length=254)

    @field_validator("current_password")
    @classmethod
    def validate_current_password(cls, value: str) -> str:
        validate_password(value)
        return value

    @field_validator("new_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class EmailChangeVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=1, max_length=256)


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(..., min_length=12, max_length=72)
    confirmation: Literal["DELETE MY ACCOUNT"]

    @field_validator("current_password")
    @classmethod
    def validate_current_password(cls, value: str) -> str:
        validate_password(value)
        return value


class RegistrationResponse(BaseModel):
    message: str


class VerificationResponse(BaseModel):
    message: str


class PasswordResetResponse(BaseModel):
    message: str


class ChangePasswordResponse(BaseModel):
    message: str
    access_token: Optional[str] = None
    token_type: Optional[str] = None
    expires_in: Optional[int] = None


class LogoutAllDevicesResponse(BaseModel):
    message: str


class ActiveSessionResponse(BaseModel):
    session_id: str
    login_at: str
    last_activity_at: str
    expires_at: str
    ip_masked: Optional[str] = None
    user_agent_family: Optional[str] = None
    is_current: bool


class ActiveSessionsResponse(BaseModel):
    sessions: List[ActiveSessionResponse]

class SecurityActivityEvent(BaseModel):
    event_id: int
    event_type: Literal[
        "login_success",
        "login_failed",
        "session_created",
        "session_revoked",
        "logout_all_sessions",
        "password_changed",
        "password_reset_requested",
        "password_reset_completed",
        "email_change_requested",
        "email_changed",
        "account_deletion_requested",
        "account_deleted",
    ]
    occurred_at: str
    metadata: Dict[str, Any]

class SecurityActivityResponse(BaseModel):
    events: List[SecurityActivityEvent]


class RevokeSessionResponse(BaseModel):
    message: str
    current_session_revoked: bool


class AccountMessageResponse(BaseModel):
    message: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    user: AuthUser


def get_auth_service() -> AuthenticationService:
    """Build the auth service without initializing vector or LLM services."""
    global _auth_service_instance
    if _auth_service_instance is None:
        database_path = Path(settings.CHROMA_PERSIST_DIR).parent / "chat_history.sqlite3"
        _auth_service_instance = AuthenticationService(
            ChatSessionStore(str(database_path))
        )
    return _auth_service_instance


def get_email_service() -> EmailService:
    return EmailService()


def _event_source(request: Request) -> str:
    """Classify a configured browser origin without retaining request headers."""
    origin = request.headers.get("origin")
    return "web" if origin in settings.CORS_ORIGINS else "api"


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> AuthUser:
    """Validate a bearer token and resolve current user and role from SQLite."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user = auth_service.resolve_access_token(credentials.credentials)
    except AuthConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthUser(**user)


def require_admin(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Require the current database role to be administrator."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return current_user


@auth_router.post(
    "/auth/register",
    response_model=RegistrationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def register(
    request: RegistrationRequest,
    http_request: Request,
    auth_service: AuthenticationService = Depends(get_auth_service),
    email_service: EmailService = Depends(get_email_service),
    email_limiter: VerificationEmailRateLimiter = Depends(
        get_verification_email_limiter
    ),
) -> RegistrationResponse:
    """Create an unverified regular account and send its verification link."""
    client = http_request.client
    client_ip = client.host if client is not None else "unknown"
    try:
        email_service.require_configuration()
    except SMTPConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email verification is not configured.",
        )
    if not email_limiter.allow(client_ip, request.email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait before requesting another verification email.",
        )

    try:
        user, token = auth_service.register(
            request.email,
            request.password,
            request.name,
        )
    except AuthConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        )
    except EmailAlreadyRegisteredError:
        return RegistrationResponse(
            message=(
                "If this address can be registered, check your inbox for "
                "verification instructions. If nothing arrives, request a resend."
            )
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid registration details.",
        )
    except sqlite3.Error as error:
        logger.error("Account registration failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account registration is temporarily unavailable.",
        )

    try:
        email_service.send_verification(user["email"], token)
    except EmailDeliveryError as error:
        logger.error("Verification email delivery failed (%s)", type(error).__name__)
    return RegistrationResponse(
        message=(
            "If this address can be registered, check your inbox for "
            "verification instructions. If nothing arrives, request a resend."
        )
    )


@auth_router.post(
    "/auth/verify-email",
    response_model=VerificationResponse,
)
async def verify_email(
    request: VerifyEmailRequest,
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> VerificationResponse:
    """Consume a single-use verification token without disclosing account data."""
    try:
        verified = auth_service.verify_email(request.token)
    except sqlite3.Error as error:
        logger.error("Email verification failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email verification could not be completed.",
        )
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification link is invalid, expired, or already used.",
        )
    return VerificationResponse(
        message="Email verified. You can now sign in."
    )


@auth_router.post(
    "/auth/resend-verification",
    response_model=VerificationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_verification(
    request: ResendVerificationRequest,
    http_request: Request,
    auth_service: AuthenticationService = Depends(get_auth_service),
    email_service: EmailService = Depends(get_email_service),
    email_limiter: VerificationEmailRateLimiter = Depends(
        get_verification_email_limiter
    ),
) -> VerificationResponse:
    """Resend verification safely without revealing whether an account exists."""
    client = http_request.client
    client_ip = client.host if client is not None else "unknown"
    try:
        email_service.require_configuration()
    except SMTPConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email verification is not configured.",
        )

    if email_limiter.allow(client_ip, request.email):
        try:
            verification = auth_service.resend_verification(request.email)
        except AuthConfigurationError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication is not configured.",
            )
        except sqlite3.Error as error:
            logger.error("Verification resend failed (%s)", type(error).__name__)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Verification request could not be completed.",
            )
        if verification is not None:
            recipient, token = verification
            try:
                email_service.send_verification(recipient, token)
            except EmailDeliveryError as error:
                logger.error(
                    "Verification email delivery failed (%s)",
                    type(error).__name__,
                )
    return VerificationResponse(
        message=(
            "If this address is eligible, verification instructions will be "
            "sent. Check your inbox or try again later."
        )
    )


@auth_router.post(
    "/auth/request-password-reset",
    response_model=PasswordResetResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_password_reset(
    request: PasswordResetRequest,
    http_request: Request,
    response: Response,
    auth_service: AuthenticationService = Depends(get_auth_service),
    email_service: EmailService = Depends(get_email_service),
    email_limiter: VerificationEmailRateLimiter = Depends(
        get_verification_email_limiter
    ),
) -> PasswordResetResponse:
    """Request a reset link without revealing account existence."""
    response.headers["Cache-Control"] = "no-store"
    client = http_request.client
    client_ip = client.host if client is not None else "unknown"
    try:
        email_service.require_configuration()
    except SMTPConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset email is not configured.",
        )

    if email_limiter.allow(client_ip, request.email):
        try:
            reset = auth_service.request_password_reset(
                request.email,
                source=_event_source(http_request),
                client_ip=client_ip,
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid password reset request.",
            )
        except sqlite3.Error as error:
            logger.error("Password reset request failed (%s)", type(error).__name__)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Password reset request could not be completed.",
            )
        if reset is not None:
            recipient, token = reset
            try:
                email_service.send_password_reset(recipient, token)
            except EmailDeliveryError as error:
                logger.error(
                    "Password reset email delivery failed (%s)",
                    type(error).__name__,
                )

    return PasswordResetResponse(
        message=(
            "If an account exists for this address, password reset "
            "instructions will be sent."
        )
    )


@auth_router.post(
    "/auth/reset-password",
    response_model=PasswordResetResponse,
)
async def reset_password(
    request: PasswordResetCompletionRequest,
    http_request: Request,
    response: Response,
    auth_service: AuthenticationService = Depends(get_auth_service),
    email_limiter: VerificationEmailRateLimiter = Depends(
        get_verification_email_limiter
    ),
) -> PasswordResetResponse:
    """Consume a one-time reset token and securely replace its password."""
    response.headers["Cache-Control"] = "no-store"
    client = http_request.client
    client_ip = client.host if client is not None else "unknown"
    if not email_limiter.allow(client_ip, request.token):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait before trying this request again.",
        )

    try:
        completed = auth_service.reset_password(
            request.token,
            request.new_password,
            source=_event_source(http_request),
            client_ip=client_ip,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid password reset request.",
        )
    except sqlite3.Error as error:
        logger.error("Password reset failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password reset could not be completed.",
        )
    if not completed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset link is invalid, expired, or already used.",
        )
    return PasswordResetResponse(message="Password updated. You can now sign in.")


@auth_router.post(
    "/auth/change-password",
    response_model=ChangePasswordResponse,
    response_model_exclude_none=True,
)
async def change_password(
    request: ChangePasswordRequest,
    http_request: Request,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> ChangePasswordResponse:
    """Change the authenticated user's password and optionally rotate sessions."""
    response.headers["Cache-Control"] = "no-store"
    try:
        token_version = auth_service.change_password(
            current_user.user_id,
            request.current_password,
            request.new_password,
            request.revoke_other_sessions,
            current_user.session_id,
            source=_event_source(http_request),
        )
    except InvalidCurrentPasswordError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    except PasswordReuseError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password must differ from the current password.",
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid password details.",
        )
    except sqlite3.Error as error:
        logger.error("Password change failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password change could not be completed.",
        )

    if current_user.session_id is not None:
        try:
            access_token = auth_service.create_access_token(
                current_user.user_id,
                token_version,
                current_user.session_id,
            )
        except AuthConfigurationError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication is not configured.",
            )
        return ChangePasswordResponse(
            message=(
                "Password changed. Other sessions were signed out; "
                "this session remains active."
            ),
            access_token=access_token,
            token_type="bearer",
            expires_in=settings.JWT_ACCESS_TOKEN_MINUTES * 60,
        )
    return ChangePasswordResponse(
        message="Password changed. Sign in again to create a tracked session."
    )


@auth_router.get(
    "/auth/sessions",
    response_model=ActiveSessionsResponse,
)
async def list_active_sessions(
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> ActiveSessionsResponse:
    """List active sessions belonging only to the authenticated user."""
    response.headers["Cache-Control"] = "no-store"
    try:
        sessions = auth_service.list_active_sessions(current_user.user_id)
    except sqlite3.Error as error:
        logger.error("Active session listing failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Sessions could not be loaded.",
        )
    return ActiveSessionsResponse(
        sessions=[
            ActiveSessionResponse(
                **session,
                is_current=session["session_id"] == current_user.session_id,
            )
            for session in sessions
        ]
    )

@auth_router.get(
    "/auth/activity",
    response_model=SecurityActivityResponse,
)
async def list_security_activity(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(get_current_user),
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> SecurityActivityResponse:
    """Return only the authenticated user's recent security activity."""
    response.headers["Cache-Control"] = "no-store"
    try:
        events = auth_service.list_security_activity(
            current_user.user_id,
            limit,
        )
    except sqlite3.Error as error:
        logger.error(
            "Security activity listing failed (%s)",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Security activity could not be loaded.",
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid activity request.",
        )
    for event in events:
        event["metadata"].pop("identifier_hash", None)
    return SecurityActivityResponse(events=events)


@auth_router.delete(
    "/auth/sessions/{session_id}",
    response_model=RevokeSessionResponse,
)
async def revoke_active_session(
    session_id: str,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> RevokeSessionResponse:
    """Revoke one of the authenticated user's active sessions."""
    response.headers["Cache-Control"] = "no-store"
    try:
        revoked = auth_service.revoke_session(current_user.user_id, session_id)
    except sqlite3.Error as error:
        logger.error("Session revocation failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session could not be revoked.",
        )
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active session not found.",
        )
    is_current = session_id == current_user.session_id
    return RevokeSessionResponse(
        message=(
            "This session is revoked. You are now signed out."
            if is_current
            else "Session revoked."
        ),
        current_session_revoked=is_current,
    )


@auth_router.post(
    "/auth/logout-all-devices",
    response_model=LogoutAllDevicesResponse,
)
async def logout_all_devices(
    http_request: Request,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> LogoutAllDevicesResponse:
    """Revoke every access token for this account, including the caller."""
    response.headers["Cache-Control"] = "no-store"
    try:
        revoked = auth_service.revoke_all_sessions(
            current_user.user_id,
            source=_event_source(http_request),
        )
    except sqlite3.Error as error:
        logger.error("Session revocation failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Sessions could not be revoked.",
        )
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found.",
        )
    return LogoutAllDevicesResponse(
        message="All active sessions, including this one, have been revoked."
    )


@auth_router.patch("/auth/profile", response_model=AuthUser)
async def update_profile(
    request: ProfileUpdateRequest,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> AuthUser:
    """Update only the authenticated user's display name."""
    response.headers["Cache-Control"] = "no-store"
    try:
        name = auth_service.update_display_name(
            current_user.user_id,
            request.name,
        )
    except sqlite3.Error as error:
        logger.error("Profile update failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Profile could not be updated.",
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account is not available.",
        )
    return current_user.model_copy(update={"name": name})


@auth_router.post(
    "/auth/request-email-change",
    response_model=AccountMessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_email_change(
    request: EmailChangeRequest,
    http_request: Request,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    auth_service: AuthenticationService = Depends(get_auth_service),
    email_service: EmailService = Depends(get_email_service),
    email_limiter: VerificationEmailRateLimiter = Depends(
        get_verification_email_limiter
    ),
) -> AccountMessageResponse:
    """Send an email verification link without changing the current address."""
    response.headers["Cache-Control"] = "no-store"
    try:
        email_service.require_configuration()
    except SMTPConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email service is not configured.",
        )

    client = http_request.client
    client_ip = client.host if client is not None else "unknown"
    if not email_limiter.allow(client_ip, request.new_email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait before requesting another verification email.",
        )

    try:
        recipient, token = auth_service.request_email_change(
            current_user.user_id,
            request.current_password,
            request.new_email,
            source=_event_source(http_request),
        )
    except InvalidCurrentPasswordError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    except EmailAlreadyInUseError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email address cannot be used.",
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid email change request.",
        )
    except sqlite3.Error as error:
        logger.error("Email change request failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email change could not be requested.",
        )

    try:
        email_service.send_email_change(recipient, token)
    except EmailDeliveryError as error:
        logger.error(
            "Email change delivery failed (%s)",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email change verification could not be delivered.",
        )
    return AccountMessageResponse(
        message=(
            "Verification instructions were sent. Your current email remains "
            "active until the new address is verified."
        )
    )


@auth_router.post(
    "/auth/verify-email-change",
    response_model=AccountMessageResponse,
)
async def verify_email_change(
    request: EmailChangeVerificationRequest,
    http_request: Request,
    response: Response,
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> AccountMessageResponse:
    """Consume an email-change verification token exactly once."""
    response.headers["Cache-Control"] = "no-store"
    try:
        changed = auth_service.verify_email_change(
            request.token,
            source=_event_source(http_request),
        )
    except sqlite3.Error as error:
        logger.error("Email change verification failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email change could not be verified.",
        )
    if not changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email change link is invalid, expired, or already used.",
        )
    return AccountMessageResponse(
        message="Email updated. Sign in again with your new address."
    )


@auth_router.delete(
    "/auth/account",
    response_model=AccountMessageResponse,
)
async def delete_account(
    request: DeleteAccountRequest,
    http_request: Request,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> AccountMessageResponse:
    """Delete the authenticated account and its owned dependent records."""
    response.headers["Cache-Control"] = "no-store"
    try:
        auth_service.delete_account(
            current_user.user_id,
            request.current_password,
            source=_event_source(http_request),
        )
    except InvalidCurrentPasswordError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid account deletion request.",
        )
    except sqlite3.Error as error:
        logger.error("Account deletion failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account could not be deleted.",
        )
    return AccountMessageResponse(
        message="Account deleted. Its owned chat history and feedback were removed."
    )


@auth_router.post("/auth/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    response: Response,
    auth_service: AuthenticationService = Depends(get_auth_service),
    login_limiter: LoginRateLimiter = Depends(get_login_rate_limiter),
) -> LoginResponse:
    """Verify account credentials and issue a short-lived access token."""
    client = http_request.client
    client_ip = client.host if client is not None else "unknown"
    account_identifier = request.email
    try:
        login_limiter.check(client_ip, account_identifier)
        result = auth_service.login(
            request.email,
            request.password,
            client_ip=client_ip,
            user_agent=http_request.headers.get("user-agent"),
            source=_event_source(http_request),
        )
    except LoginRateLimitExceeded as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Login temporarily unavailable. Please try again later.",
            headers={"Retry-After": str(error.retry_after)},
        )
    except AuthConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        )
    except InvalidCredentialsError:
        login_limiter.record_failure(client_ip, account_identifier)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    except EmailNotVerifiedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify your email address before signing in.",
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid login details.",
        )
    except sqlite3.Error as error:
        logger.error("Account login failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login is temporarily unavailable.",
        )
    response.headers["Cache-Control"] = "no-store"
    login_limiter.record_success(account_identifier)
    return LoginResponse(**result)


@auth_router.get("/auth/me", response_model=AuthUser)
async def get_authenticated_user(
    current_user: AuthUser = Depends(get_current_user),
) -> AuthUser:
    """Return the authenticated user's safe identity fields."""
    return current_user
