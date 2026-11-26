"""SMTP delivery for email-verification links."""

import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlencode, urlsplit

from backend.config import settings


class SMTPConfigurationError(RuntimeError):
    """Raised when required email settings are missing or unsafe."""


class EmailDeliveryError(RuntimeError):
    """Raised when SMTP cannot accept an email message."""


class EmailService:
    """Send account emails without exposing token material in logs."""

    @staticmethod
    def require_configuration() -> None:
        if not settings.SMTP_HOST.strip():
            raise SMTPConfigurationError("SMTP_HOST is not configured.")
        if not settings.SMTP_FROM_EMAIL.strip():
            raise SMTPConfigurationError("SMTP_FROM_EMAIL is not configured.")
        if not settings.FRONTEND_BASE_URL.strip():
            raise SMTPConfigurationError("FRONTEND_BASE_URL is not configured.")
        if bool(settings.SMTP_USERNAME.strip()) != bool(
            settings.SMTP_PASSWORD.get_secret_value()
        ):
            raise SMTPConfigurationError(
                "SMTP_USERNAME and SMTP_PASSWORD must be configured together."
            )
        if settings.ENVIRONMENT == "production" and not settings.SMTP_USE_TLS:
            raise SMTPConfigurationError(
                "SMTP_USE_TLS must be enabled in production."
            )

        frontend = urlsplit(settings.FRONTEND_BASE_URL)
        if (
            frontend.scheme not in ("http", "https")
            or not frontend.netloc
            or frontend.username is not None
            or frontend.password is not None
            or frontend.query
            or frontend.fragment
            or (
                settings.ENVIRONMENT == "production"
                and frontend.scheme != "https"
            )
        ):
            raise SMTPConfigurationError(
                "FRONTEND_BASE_URL must be a valid frontend origin or base path."
            )

    @staticmethod
    def _verification_link(token: str) -> str:
        base_url = settings.FRONTEND_BASE_URL.rstrip("/")
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}{urlencode({'verify_email': token})}"

    @staticmethod
    def _password_reset_link(token: str) -> str:
        base_url = settings.FRONTEND_BASE_URL.rstrip("/")
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}{urlencode({'reset_password': token})}"

    @staticmethod
    def _email_change_link(token: str) -> str:
        base_url = settings.FRONTEND_BASE_URL.rstrip("/")
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}{urlencode({'verify_email_change': token})}"

    def send_verification(self, recipient: str, token: str) -> None:
        """Deliver a short-lived single-use link over configured SMTP."""
        self.require_configuration()
        message = EmailMessage()
        message["Subject"] = "Verify your Telecom Troubleshooting AI account"
        message["From"] = settings.SMTP_FROM_EMAIL
        message["To"] = recipient
        message.set_content(
            "Use the following link to verify your email address. "
            f"It expires in {settings.EMAIL_VERIFICATION_MINUTES} minutes.\n\n"
            f"{self._verification_link(token)}\n\n"
            "If you did not request this account, you can ignore this message."
        )

        try:
            with smtplib.SMTP(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=15,
            ) as client:
                if settings.SMTP_USE_TLS:
                    client.starttls(context=ssl.create_default_context())
                if settings.SMTP_USERNAME:
                    client.login(
                        settings.SMTP_USERNAME,
                        settings.SMTP_PASSWORD.get_secret_value(),
                    )
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise EmailDeliveryError(
                "Verification email could not be delivered."
            ) from error

    def send_password_reset(self, recipient: str, token: str) -> None:
        """Deliver a short-lived password reset link over configured SMTP."""
        self.require_configuration()
        message = EmailMessage()
        message["Subject"] = "Reset your Telecom Troubleshooting AI password"
        message["From"] = settings.SMTP_FROM_EMAIL
        message["To"] = recipient
        message.set_content(
            "Use the following link to reset your password. "
            f"It expires in {settings.PASSWORD_RESET_MINUTES} minutes.\n\n"
            f"{self._password_reset_link(token)}\n\n"
            "If you did not request a password reset, you can ignore this message."
        )

        try:
            with smtplib.SMTP(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=15,
            ) as client:
                if settings.SMTP_USE_TLS:
                    client.starttls(context=ssl.create_default_context())
                if settings.SMTP_USERNAME:
                    client.login(
                        settings.SMTP_USERNAME,
                        settings.SMTP_PASSWORD.get_secret_value(),
                    )
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise EmailDeliveryError(
                "Password reset email could not be delivered."
            ) from error

    def send_email_change(self, recipient: str, token: str) -> None:
        """Deliver a one-time email-change verification link."""
        self.require_configuration()
        message = EmailMessage()
        message["Subject"] = "Verify your new Telecom Troubleshooting AI email"
        message["From"] = settings.SMTP_FROM_EMAIL
        message["To"] = recipient
        message.set_content(
            "Use the following link to verify your new email address. "
            f"It expires in {settings.EMAIL_VERIFICATION_MINUTES} minutes.\n\n"
            f"{self._email_change_link(token)}\n\n"
            "If you did not request this change, you can ignore this message."
        )

        try:
            with smtplib.SMTP(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=15,
            ) as client:
                if settings.SMTP_USE_TLS:
                    client.starttls(context=ssl.create_default_context())
                if settings.SMTP_USERNAME:
                    client.login(
                        settings.SMTP_USERNAME,
                        settings.SMTP_PASSWORD.get_secret_value(),
                    )
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise EmailDeliveryError(
                "Email change verification could not be delivered."
            ) from error
