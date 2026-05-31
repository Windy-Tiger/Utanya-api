# ADD THIS ROUTE to your existing routers/rag.py
# Place it after the /ingest/bulletin endpoint

@router.post("/ingest/bulletin-json")
@limiter.limit("10/minute")
async def ingest_bulletin_json(
    request: Request,
    file: UploadFile = File(...),
    api_key: str = Security(verify_api_key)
):
    """
    Ingest a pre-extracted bulletin JSON file.
    This is the preferred method: Claude reads the PDF natively in the conversation
    and produces a structured JSON, which is then ingested here with 100% accuracy.
    No vision API costs. No OCR errors.
    """
    from bulletin_json_converter import json_to_chunks

    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be a JSON")

    contents = await file.read()

    try:
        bulletin_data = json.loads(contents)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    bulletin_num = bulletin_data.get("bulletin_number", "unknown")
    date = bulletin_data.get("date", "unknown")
    title = bulletin_data.get("title", f"BODIVA Boletim {bulletin_num} - {date}")

    chunks = json_to_chunks(bulletin_data)

    doc_id = await store_document_and_chunks(
        title=title,
        doc_type="bulletin",
        doc_date=date,
        source_url=None,
        chunks=chunks
    )

    return {
        "status": "ingested",
        "document_id": doc_id,
        "bulletin_number": bulletin_num,
        "date": date,
        "chunks_created": len(chunks),
        "title": title,
        "sections": list(set(c['metadata'].get('section', '') for c in chunks))
    }
