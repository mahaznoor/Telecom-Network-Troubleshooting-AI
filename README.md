# Telecom Network Troubleshooting AI

Telecom Network Troubleshooting AI is a local web application for searching
telecom manuals and answering troubleshooting questions with a
retrieval-augmented generation (RAG) pipeline. It combines a FastAPI backend,
Streamlit interface, SQLite account/chat storage, ChromaDB vector search,
Hugging Face embeddings, and a Groq-hosted language model.

## Architecture and technology

- **API:** FastAPI and Pydantic, under `backend/api/`
- **UI:** Streamlit, under `frontend/`
- **PDF ingestion:** PyMuPDF extraction and LangChain text splitting
- **Embeddings:** `BAAI/bge-small-en-v1.5` through Hugging Face
- **Vector store:** persistent ChromaDB at `CHROMA_PERSIST_DIR`
- **Answer generation:** Groq through `langchain-groq`
- **Accounts, sessions, chat, feedback, and audit events:** SQLite at
  `chat_history.sqlite3` beside the configured Chroma directory

The application does not require Docker. SQLite migrations are additive and
run when the backend initializes its auth/chat store. Normal startup does not
recreate or clear SQLite or ChromaDB data.

## Windows setup

Run these commands from the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell prevents environment activation, call the virtual-environment
interpreter directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Edit `.env` locally. Do not copy real secrets into source control. At minimum,
configure a Groq API key and a private JWT signing key. Generate independent
secrets locally:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use one generated value for `JWT_SECRET_KEY` and a different value for
`AUDIT_IDENTIFIER_HMAC_SECRET`. Each must contain at least 32 bytes. Keep
`.env` private; `.env.example` contains placeholders only.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `GROQ_API_KEY` | Groq API credential for answer generation | unset |
| `LLM_MODEL` | Model identifier sent to Groq | `llama-3.1-70b-versatile` |
| `JWT_SECRET_KEY` | HS256 signing secret; at least 32 bytes | unset |
| `AUDIT_IDENTIFIER_HMAC_SECRET` | Separate key for keyed login-identifier hashes; at least 32 bytes | unset |
| `ENVIRONMENT` | `development` or `production` security mode | `development` |
| `HOST`, `PORT` | Backend bind settings | `0.0.0.0`, `8000` |
| `CORS_ORIGINS` | Explicit browser origins; production requires HTTPS | localhost Streamlit origins |
| `API_BASE_URL` | Streamlit-to-FastAPI base URL, including `/api/v1` | `http://localhost:8000/api/v1` |
| `JWT_ACCESS_TOKEN_MINUTES` | JWT lifetime | `60` |
| `BCRYPT_ROUNDS` | bcrypt work factor (10–14) | `12` |
| `LOGIN_IP_MAX_FAILURES` | Failed login attempts per IP window | `20` |
| `LOGIN_ACCOUNT_MAX_FAILURES` | Failed login attempts before account lockout | `5` |
| `LOGIN_FAILURE_WINDOW_SECONDS` | Login failure tracking period | `900` |
| `LOGIN_ACCOUNT_LOCKOUT_SECONDS` | Account lockout period | `900` |
| `SMTP_HOST`, `SMTP_PORT` | SMTP server used for account email | unset, `587` |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Optional SMTP credentials; configure both or neither | unset |
| `SMTP_FROM_EMAIL` | Sender address for account messages | unset |
| `SMTP_USE_TLS` | Enable STARTTLS; required in production | `true` |
| `FRONTEND_BASE_URL` | Streamlit URL used to construct verification/reset links | unset |
| `EMAIL_VERIFICATION_MINUTES` | Verification-link lifetime (15–30 minutes) | `20` |
| `PASSWORD_RESET_MINUTES` | Password-reset-link lifetime (15–60 minutes) | `30` |
| `VERIFICATION_EMAIL_IP_MAX_PER_HOUR` | Account-email requests per IP per hour | `10` |
| `VERIFICATION_EMAIL_ADDRESS_MAX_PER_HOUR` | Account-email requests per address per hour | `3` |
| `DATA_DIR` | Source/manual PDF directory | `./data/manuals` |
| `CHROMA_PERSIST_DIR` | Persistent ChromaDB directory | `./storage/chroma` |
| `CHROMA_COLLECTION_NAME` | ChromaDB collection | `telecom_manuals` |
| `EMBEDDING_MODEL` | Hugging Face embedding model | `BAAI/bge-small-en-v1.5` |
| `EMBEDDING_DIMENSION` | Configured vector size | `384` |
| `EMBEDDING_DEVICE` | Embedding device (`cpu` or supported accelerator) | `cpu` |
| `CHUNK_SIZE`, `CHUNK_OVERLAP` | PDF text chunking parameters | `1000`, `200` |
| `TOP_K` | Default retrieval count | `5` |
| `SIMILARITY_THRESHOLD` | Maximum Chroma distance; lower is closer | `0.7` |
| `EVAL_QUESTIONS_FILE` | Example evaluation-question data file | `./evaluation/test_questions.json` |

The backend and Streamlit load configuration from environment variables or
`.env`. For a deployed frontend and backend on separate hosts, set
`API_BASE_URL` to the reachable API origin. Configure `CORS_ORIGINS` for any
browser-based clients that call the API directly; Streamlit's server-side API
requests do not rely on browser CORS.

SMTP must be reachable for registration verification, password reset, and
email changes. A local SMTP capture server may be used for development, but
the project does not start or require one. Hugging Face model files may need
to be downloaded on first use; Groq answer generation requires valid
credentials and network access.

## Start the application

Keep both commands running in separate PowerShell terminals, from the
repository root with the virtual environment active.

Backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Streamlit:

```powershell
.\.venv\Scripts\python.exe -m streamlit run frontend\streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

Open `http://127.0.0.1:8501`. Interactive API documentation is available at
`http://127.0.0.1:8000/docs`; OpenAPI JSON is at
`http://127.0.0.1:8000/openapi.json`.

For production, disable development reload, terminate TLS at a trusted
ingress/reverse proxy, restrict access to both services, use exact HTTPS
origins, and keep query-string access logs from recording
`verify_email`, `verify_email_change`, or `reset_password` tokens.

## User and administrator workflows

### Accounts and security

- Public registration creates a regular, unverified account. Email verification
  is required before login.
- Sign-in supports generic forgot-password requests. Reset links are single-use
  and expire; SQLite stores only the SHA-256 token hash.
- Settings displays profile information, supports display-name and verified
  email changes, and provides password change, account deletion, active
  session revocation, sign-out-all-devices, and recent security activity.
- Passwords use bcrypt. Password changes require the current password and
  revoke other sessions. Revoked sessions cannot access protected endpoints.
- Audit-event metadata uses per-event allowlists, masks IP addresses, stores
  normalized client families, and never stores raw JWTs or reset tokens.
- Login and verification-email throttles are bounded and process-local. For
  multiple workers or replicas, enforce shared throttling at a trusted gateway
  as well.

Account deletion requires the password and the exact phrase
`DELETE MY ACCOUNT`. It removes that user's SQLite account and owned
conversations/messages/feedback using foreign-key behavior. Shared ChromaDB
content and source PDFs are not removed.

### Initial administrator

1. Register and verify the intended account.
2. Verify the account owner's identity through a separate trusted channel.
3. Stop the application and back up the SQLite database.
4. From the repository root run `python -m backend.admin_bootstrap`.
5. Enter the existing account email and type the exact `PROMOTE <email>`
   confirmation.

The command only promotes an existing verified regular account. Public
registration cannot assign administrator privileges.

### Documents and troubleshooting

- Administrators can ingest PDFs from `DATA_DIR` or upload PDFs through the
  Knowledge Base. Browser uploads are sent to FastAPI as multipart data and
  retained under `DATA_DIR\uploads`; they are not discarded after indexing.
  An upload batch is limited to 200 MiB total and 100 PDFs.
- Text is extracted page-by-page, split into chunks, embedded, and persisted
  in the configured Chroma collection. Content fingerprints skip already
  indexed PDFs.
- Users can search indexed documentation, ask follow-up questions, review
  citations, save conversations, and clear their own chat history.
- Administrators can remove a selected document's chunks from the shared index
  or, after explicit confirmation, delete the entire Chroma collection. These
  actions do not delete the source PDFs. A later ingestion may index a retained
  PDF again.
- When retrieval returns no relevant source, the answer prompt instructs the
  model to disclose insufficient documentation rather than invent technical
  steps. Model output still depends on the configured external provider.

### Feedback and administration

Signed-in users can rate their own persisted assistant responses. Feedback
summaries and recent comments are restricted to administrators. The health
endpoint reports local configuration/database state; it does not prove that
Groq or the embedding provider is reachable.

## SQLite and ChromaDB data

With the default paths, the application uses:

- SQLite: `storage\chat_history.sqlite3`
- ChromaDB: `storage\chroma`
- Source PDFs: `data\manuals`
- Newly uploaded PDFs: `data\manuals\uploads`

SQLite schema changes are additive and applied when the auth/chat store is
initialized. Existing user, chat, feedback, verification, reset, and session
records are not reset by startup. Legacy unowned chat rows remain inaccessible
to regular accounts until an operator performs a verified ownership transfer.

ChromaDB is persistent. Normal startup, health checks, search, and ingestion do
not wipe the collection. Deletion is an explicit administrator action. Back up
SQLite and ChromaDB before operational maintenance.

## API overview

All paths below are prefixed with `/api/v1`.

| Method | Endpoint | Access |
|---|---|---|
| `GET` | `/health` | Public |
| `POST` | `/auth/register`, `/auth/verify-email`, `/auth/resend-verification` | Public |
| `POST` | `/auth/login`, `/auth/request-password-reset`, `/auth/reset-password` | Public |
| `GET` | `/auth/me`, `/auth/sessions`, `/auth/activity` | Signed in |
| `DELETE` | `/auth/sessions/{session_id}` | Signed in; own sessions only |
| `POST` | `/auth/change-password`, `/auth/logout-all-devices`, `/auth/request-email-change` | Signed in |
| `PATCH` | `/auth/profile` | Signed in |
| `POST` | `/auth/verify-email-change` | Public, one-time token |
| `DELETE` | `/auth/account` | Signed in; password and confirmation required |
| `POST` | `/chat`, `/feedback` | Signed in |
| `GET` | `/chat/sessions`, `/chat/sessions/{session_id}`, `/search`, `/documents` | Signed in |
| `DELETE` | `/chat`, `/chat/{session_id}` | Signed in; own conversations only |
| `POST` | `/ingest`, `/ingest/upload` | Administrator |
| `DELETE` | `/documents/{document_id}`, `/vectordb` | Administrator |
| `GET` | `/feedback/summary` | Administrator |

Use `/docs` for request/response schemas and interactive API exploration.

## Validation

Run the complete existing unittest suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Run deterministic offline response-fixture checks:

```powershell
.\.venv\Scripts\python.exe evaluation\evaluate.py
```

The offline evaluation checks bundled examples; it does not call Groq, load
real manuals, or measure live RAG relevance. Live API/email/model integration
requires the corresponding credentials and services.

## Known limitations before deployment

- Login and verification-email rate limits are process-local, not shared across
  multiple workers or hosts.
- The default health check reports configuration/local availability; it does
  not probe Groq or run embedding inference.
- Live model, SMTP, and end-to-end uploaded-document checks require external
  credentials/services and are not represented by the offline evaluation
  fixtures.
- Existing legacy chat rows without an owner remain unassigned by design;
  ownership recovery is a manual, backed-up operator task.
- Production deployment still requires operational TLS, backups, shared rate
  limiting, monitoring, and validation of the chosen Groq model identifier.
