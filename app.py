import os
import json
import sqlite3
import boto3
import chromadb
from fastapi import FastAPI, Request, Header, HTTPException

app = FastAPI()

API_KEY = os.environ.get("API_KEY", "")

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"
EMBED_MODEL = "amazon.titan-embed-text-v2:0"

DB_PATH = "memory.db"
chroma = chromadb.PersistentClient(path="./chroma_db")
collection = chroma.get_or_create_collection("memories")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


init_db()


def embed(text: str) -> list:
    response = bedrock.invoke_model(
        modelId=EMBED_MODEL,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"inputText": text}),
    )
    return json.loads(response["body"].read())["embedding"]


def remember(text: str, session_id: str, role: str, msg_id: int):
    collection.upsert(
        ids=[str(msg_id)],
        embeddings=[embed(text)],
        documents=[text],
        metadatas=[{"session_id": session_id, "role": role}],
    )


def recall(query: str, n: int = 10) -> list:
    if collection.count() == 0:
        return []
    results = collection.query(
        query_embeddings=[embed(query)],
        n_results=min(n, collection.count()),
    )
    return results["documents"][0] if results["documents"] else []


def get_history(session_id: str, limit: int = 20) -> list:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT role, content FROM conversations WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


def save_message(session_id: str, role: str, content: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "INSERT INTO conversations (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role, content),
    )
    msg_id = cur.lastrowid
    conn.commit()
    conn.close()
    return msg_id


def verify_key(authorization: str = Header(None)):
    if not API_KEY:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing API key")
    if authorization[7:] != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/")
def hello():
    return {"status": "alive"}


@app.post("/chat")
async def chat(request: Request, authorization: str = Header(None)):
    verify_key(authorization)
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", "default")

    msg_id = save_message(session_id, "user", message)
    remember(message, session_id, "user", msg_id)

    memories = recall(message)
    history = get_history(session_id)

    system = ""
    if memories:
        system = "Relevant memories from past conversations:\n" + "\n".join(f"- {m}" for m in memories)

    messages_payload = history

    response = bedrock.invoke_model(
        modelId=DEFAULT_MODEL,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": system,
            "messages": messages_payload,
        }),
    )

    result = json.loads(response["body"].read())
    assistant_msg = result["content"][0]["text"]

    aid = save_message(session_id, "assistant", assistant_msg)
    remember(assistant_msg, session_id, "assistant", aid)

    return {"response": assistant_msg, "session_id": session_id}


@app.get("/history/{session_id}")
def history(session_id: str, authorization: str = Header(None)):
    verify_key(authorization)
    return {"messages": get_history(session_id)}


@app.delete("/history/{session_id}")
def clear_history(session_id: str, authorization: str = Header(None)):
    verify_key(authorization)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()
    return {"cleared": session_id}


@app.get("/recall")
def recall_endpoint(q: str, n: int = 10, authorization: str = Header(None)):
    verify_key(authorization)
    return {"memories": recall(q, n)}


@app.post("/agent")
async def agent(request: Request, authorization: str = Header(None)):
    verify_key(authorization)
    body = await request.json()
    message = body.get("message", "")

    memories = recall(message)
    context = ""
    if memories:
        context = "Relevant memories:\n" + "\n".join(f"- {m}" for m in memories)

    from tools.orchestrator import orchestrate
    result = await orchestrate(message, system_context=context)
    return result
