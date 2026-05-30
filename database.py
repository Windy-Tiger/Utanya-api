import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            os.environ.get("DATABASE_URL"),
            min_size=1,
            max_size=10
        )
    return _pool

async def get_connection():
    pool = await get_pool()
    return await pool.acquire()
