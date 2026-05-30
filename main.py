from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Utanya API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from routers import rag
app.include_router(rag.router, prefix="/rag", tags=["RAG"])

@app.get("/")
async def root():
    return {"status": "Utanya API running", "version": "1.0.0"}