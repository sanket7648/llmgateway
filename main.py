import os
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.responses import JSONResponse
import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import logging
logger = logging.getLogger("uvicorn.error")

# Load environment variables from a .env file (if it exists)
load_dotenv()

# --- Strict Configuration (Zero Hardcoded Secrets) ---
# The app will fail to start immediately if these are not set in the host environment.
PROVIDER_API_KEY = os.getenv("GEMINI_API_KEY")
if not PROVIDER_API_KEY:
    raise ValueError("FATAL: GEMINI_API_KEY environment variable is not set.")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("FATAL: DATABASE_URL environment variable is not set.")

PROVIDER_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

# --- Database Setup ---
def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    # IF NOT EXISTS guarantees this safely runs on every startup without overwriting
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            virtual_key TEXT PRIMARY KEY,
            budget_usd REAL,
            spend_usd REAL DEFAULT 0.0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            virtual_key TEXT,
            model TEXT,
            tokens INTEGER,
            cost_usd REAL,
            timestamp TIMESTAMP
        )
    """)
    cursor.execute("""
        INSERT INTO keys (virtual_key, budget_usd, spend_usd) 
        VALUES ('rentok-test-key-123', 1.00, 0.0)
        ON CONFLICT (virtual_key) DO NOTHING
    """)
    conn.commit()
    cursor.close()
    conn.close()

# --- Application Startup Lifecycle ---
# --- Application Startup Lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("INFO:     Connecting to PostgreSQL database and verifying tables...")
    init_db()
    print("INFO:     Database initialized successfully.")
    yield
    print("INFO:     Shutting down...")

app = FastAPI(title="Minimal LLM Gateway", lifespan=lifespan)

# --- Helper Functions ---
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()

def estimate_cost(model: str, total_tokens: int) -> float:
    return (total_tokens / 1000.0) * 0.0001

# --- Core Proxy Endpoint ---
@app.post("/v1/chat/completions")
async def proxy_chat_completion(
    request: Request, 
    authorization: str = Header(None),
    db = Depends(get_db)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    virtual_key = authorization.split("Bearer ")[1]

    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT budget_usd, spend_usd FROM keys WHERE virtual_key = %s", (virtual_key,))
    row = cursor.fetchone()
    
    if not row:
        raise HTTPException(status_code=401, detail="Invalid virtual key")
    
    budget, spend = row["budget_usd"], row["spend_usd"]
    if spend >= budget:
        raise HTTPException(status_code=402, detail="Budget exceeded for this key")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model_requested = body.get("model", "gemini-3.8-flash")
    body["model"] = model_requested

    headers = {
        "Authorization": f"Bearer {PROVIDER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(PROVIDER_URL, json=body, headers=headers, timeout=30.0)
            
            if response.status_code == 503:
                body["model"] = "gemini-3.5-flash-lite"
                response = await client.post(PROVIDER_URL, json=body, headers=headers, timeout=30.0)
                
            response.raise_for_status()
            provider_data = response.json()
            
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="LLM provider timed out")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Provider error: {e.response.text}")
        except Exception as e:
            raise HTTPException(status_code=502, detail="Bad Gateway: Failed to reach provider")

    usage = provider_data.get("usage", {})
    total_tokens = usage.get("total_tokens", 0)
    cost = estimate_cost(model_requested, total_tokens)

    timestamp = datetime.datetime.utcnow()
    cursor.execute("""
        INSERT INTO logs (virtual_key, model, tokens, cost_usd, timestamp)
        VALUES (%s, %s, %s, %s, %s)
    """, (virtual_key, model_requested, total_tokens, cost, timestamp))
    
    cursor.execute("""
        UPDATE keys SET spend_usd = spend_usd + %s WHERE virtual_key = %s
    """, (cost, virtual_key))
    
    db.commit()
    cursor.close()

    return JSONResponse(content=provider_data)

# --- Admin Endpoint ---
@app.get("/usage")
def get_usage(key: str, db = Depends(get_db)):
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT spend_usd, budget_usd FROM keys WHERE virtual_key = %s", (key,))
    row = cursor.fetchone()
    cursor.close()
    if not row:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"key": key, "spend_usd": row["spend_usd"], "budget_usd": row["budget_usd"]}