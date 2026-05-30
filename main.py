import os
from fastapi import FastAPI, Security, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv

load_dotenv()

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Utanya API", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
    expected = os.environ.get("2026Utanya232811!")
    if not expected or api_key != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return api_key

app.state.verify_api_key = verify_api_key

from routers import rag
app.include_router(rag.router, prefix="/rag", tags=["RAG"])

@app.get("/")
async def root():
    return {"status": "Utanya API running", "version": "1.0.0"}