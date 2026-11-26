"""Operator-only CLI for promoting a verified existing account to administrator."""

from pathlib import Path

from backend.config import settings
from backend.services.auth_service import normalize_email
from backend.services.session_store import ChatSessionStore


def promote_verified_account(
    store: ChatSessionStore,
    email: str,
    confirmation: str,
) -> bool:
    """Promote an explicitly confirmed existing account without a public endpoint."""
    normalized_email = normalize_email(email)
    if confirmation != f"PROMOTE {normalized_email}":
        raise ValueError("Promotion confirmation did not match.")
    user = store.get_user_by_email(normalized_email)
    if user is None:
        raise ValueError("No matching account exists; no changes were made.")
    if not user["email_verified"]:
        raise ValueError("Account email must be verified before promotion.")
    if user["role"] != "user":
        raise ValueError("Account is already an administrator; no changes were made.")
    if not store.promote_user_to_admin(normalized_email):
        raise RuntimeError("Account promotion did not complete.")
    return True


def main() -> None:
    """Prompt an operator to promote one already-verified account."""
    print(
        "Before continuing, stop the application, back up its SQLite database, "
        "and independently verify this account owner's identity."
    )
    email = normalize_email(input("Existing account email to promote: "))
    confirmation = input(f"Type 'PROMOTE {email}' to confirm: ")
    if confirmation != f"PROMOTE {email}":
        raise SystemExit("Promotion confirmation did not match; no changes made.")
    database_path = Path(settings.CHROMA_PERSIST_DIR).parent / "chat_history.sqlite3"
    store = ChatSessionStore(str(database_path))
    promote_verified_account(store, email, confirmation)
    print("Administrator role granted to the confirmed existing account.")


if __name__ == "__main__":
    main()
