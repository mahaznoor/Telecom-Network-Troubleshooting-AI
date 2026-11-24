
# Telecom Network Troubleshooting AI

## Project Overview
Build a professional AI-powered telecom troubleshooting
assistant using Python, LangChain and RAG.

## Technology Stack
- Python 3.11+
- LangChain
- Groq API
- GPT-OSS 120B (if available)
- HuggingFace BGE Embeddings
- ChromaDB
- FastAPI
- Streamlit
- RAGAS
- PyMuPDF

## Main Features
1. Upload telecom PDF manuals.
2. Extract text from PDF documents.
3. Split documents into chunks.
4. Generate embeddings.
5. Store vectors in ChromaDB.
6. Retrieve relevant document chunks.
7. Generate answers using Groq LLM.
8. Display document source and page number.
9. Maintain chat history.
10. Evaluate RAG performance.

## Supported Categories
- 4G Network
- 5G Network
- Router Troubleshooting
- ONT Troubleshooting
- OLT Troubleshooting
- Fiber Optic Troubleshooting

## Architecture
PDF Documents
    -> Document Loader
    -> Text Splitter
    -> Embeddings
    -> ChromaDB
    -> Retriever
    -> LangChain RAG Chain
    -> Groq LLM
    -> Answer with Sources

## Coding Rules
- Use modular Python code.
- Use environment variables for API keys.
- Never hardcode API keys.
- Add error handling.
- Use type hints.
- Add docstrings.
- Keep code beginner-friendly.
- Do not create unnecessary files.
- Do not use deprecated LangChain APIs.
- Keep all imports consistent.
- Make each module independently understandable.

## Important Rules
- Answers must be grounded in retrieved documents.
- Never invent telecom troubleshooting procedures.
- Include source document and page references.
- If information is unavailable, say so clearly.
- Do not perform real network configuration changes.

## Development Instructions
Build the application phase by phase.
First implement PDF ingestion.
Then embeddings and ChromaDB.
Then RAG and Groq.
Then FastAPI.
Finally Streamlit and evaluation.

Do not generate the entire project at once.
Test each phase before moving forward.
