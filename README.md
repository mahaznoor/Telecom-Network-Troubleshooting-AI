# Telecom Network Troubleshooting AI

An AI-powered troubleshooting assistant for telecom network engineers. This system uses Retrieval-Augmented Generation (RAG) to provide accurate, context-aware answers to network troubleshooting questions based on technical manuals and documentation.

## Features

- 📚 **Document Ingestion**: Load and index telecom technical manuals (PDFs)
- 🔍 **Semantic Search**: Find relevant documentation using vector embeddings
- 🤖 **RAG Pipeline**: Generate accurate answers grounded in your documentation
- 💬 **Chat Interface**: Streamlit-based conversational UI
- 📊 **Session Management**: Maintain conversation context across queries
- 📈 **Evaluation Framework**: Test and measure answer quality

## Architecture

```
├── backend/                 # FastAPI backend
│   ├── api/                # REST API routes
│   ├── ingestion/          # Document loading and processing
│   ├── vectorstore/        # ChromaDB vector storage
│   ├── rag/                # RAG pipeline (retriever, chain, prompts)
│   └── services/           # Business logic orchestration
├── frontend/               # Streamlit frontend
├── data/manuals/           # Source PDF documents
├── storage/chroma/         # Vector database persistence
└── evaluation/             # Testing and evaluation
```

## Quick Start

### Prerequisites

- Python 3.10+
- OpenAI API key

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd Telecom-Network-Troubleshooting-AI
```

2. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment:
```bash
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
```

5. Add telecom manuals to `data/manuals/`

### Running the Application

1. Start the backend API:
```bash
cd backend
python main.py
# Or: uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

2. In a new terminal, start the frontend:
```bash
cd frontend
streamlit run streamlit_app.py
```

3. Open http://localhost:8501 in your browser

## Usage

### Ingesting Documents

1. Place PDF manuals in `data/manuals/`
2. Use the Streamlit UI to upload and ingest documents
3. Or call the API directly:
```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"directory": "./data/manuals"}'
```

### Asking Questions

Use the chat interface or API:
```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "How to troubleshoot BGP session flaps?", "top_k": 5}'
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/health` | Health check |
| POST | `/api/v1/chat` | Ask a troubleshooting question |
| POST | `/api/v1/ingest` | Ingest PDF documents |
| DELETE | `/api/v1/sessions/{id}` | Clear session history |

## Configuration

Key settings in `.env`:

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key (required) | - |
| `LLM_MODEL` | LLM model to use | `gpt-4-turbo-preview` |
| `EMBEDDING_MODEL` | Embedding model | `text-embedding-3-small` |
| `CHUNK_SIZE` | Text chunk size | `1000` |
| `CHUNK_OVERLAP` | Chunk overlap | `200` |
| `TOP_K` | Retrieval count | `5` |
| `SIMILARITY_THRESHOLD` | Minimum similarity | `0.7` |

## Evaluation

Run the evaluation suite:
```bash
cd evaluation
python evaluate.py
```

Add test questions to `evaluation/test_questions.json`.

## Project Structure Details

### Backend Modules

- **`api/routes.py`**: FastAPI routes for chat, ingestion, health
- **`ingestion/pdf_loader.py`**: Load PDF documents using PyPDF
- **`ingestion/text_splitter.py`**: Split documents into chunks
- **`ingestion/embeddings.py`**: OpenAI embeddings configuration
- **`vectorstore/chroma_db.py`**: ChromaDB wrapper with search methods
- **`rag/retriever.py`**: Custom retriever with MMR and filtering
- **`rag/chain.py`**: RAG chain with history awareness
- **`rag/prompts.py`**: Prompt templates for QA, condensation, evaluation
- **`services/chat_service.py`**: Main service orchestrating the pipeline

### Frontend

- **`streamlit_app.py`**: Streamlit chat interface with document upload

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `pytest`
5. Submit a pull request

## License

MIT License - see LICENSE file for details.

## Support

For issues and questions, please open a GitHub issue.