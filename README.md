# Utanya API

BODIVA capital markets RAG system and backend for Utanya.

## Setup

1. Copy `.env.example` to `.env` and fill in your values
2. Install dependencies: `pip install -r requirements.txt`
3. Run locally: `uvicorn main:app --reload`
4. After deploying to Railway, call `POST /rag/setup` once to initialize the database

## Environment Variables

- `DATABASE_URL` — Railway Postgres connection string
- `VOYAGE_API_KEY` — Voyage AI API key (embeddings)
- `ANTHROPIC_API_KEY` — Anthropic API key (generation)

## API Endpoints

- `GET /` — Health check
- `POST /rag/setup` — Initialize database (run once)
- `POST /rag/ingest/text` — Ingest plain text
- `POST /rag/ingest/pdf` — Upload and ingest a PDF
- `POST /rag/query` — Ask a question
- `GET /rag/documents` — List all documents
- `DELETE /rag/documents/{id}` — Remove a document
