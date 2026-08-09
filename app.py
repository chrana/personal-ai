import os
import time
import json
import asyncio
import sqlite3
import boto3
import chromadb
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse
from tools.monitoring import log_request

app = FastAPI()


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start) * 1000
    log_request(request.method, request.url.path, response.status_code, duration_ms)
    return response

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


@app.get("/ui", response_class=HTMLResponse)
def ui():
    return """<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Personal AI</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, system-ui, sans-serif; background: #1a1a2e; color: #eee; height: 100vh; display: flex; flex-direction: column; }
#chat { flex: 1; overflow-y: auto; padding: 16px; }
.msg { margin: 8px 0; padding: 10px 14px; border-radius: 12px; max-width: 85%; white-space: pre-wrap; word-wrap: break-word; line-height: 1.5; }
.user { background: #0f3460; margin-left: auto; }
.assistant { background: #16213e; }
.error { background: #3d1515; border: 1px solid #e94560; }
.progress { font-size: 13px; color: #8892b0; padding: 6px 12px; margin: 4px 0; }
.progress .step { display: flex; align-items: center; gap: 8px; padding: 2px 0; }
.progress .step::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: #4ecdc4; flex-shrink: 0; }
.progress .step.active::before { animation: pulse 1s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
#input-area { display: flex; padding: 12px; gap: 8px; background: #0f0f1a; }
#msg { flex: 1; padding: 12px; border-radius: 8px; border: 1px solid #333; background: #1a1a2e; color: #eee; font-size: 16px; }
#send { padding: 12px 20px; border-radius: 8px; border: none; background: #e94560; color: #fff; font-size: 16px; cursor: pointer; }
#send:disabled { opacity: 0.5; }
</style>
</head><body>
<div id="chat"></div>
<div id="input-area">
  <input id="msg" type="text" placeholder="Ask me anything..." autocomplete="off">
  <button id="send" onclick="send()">Send</button>
</div>
<script>
const KEY = localStorage.getItem('api_key') || prompt('Enter your API key:');
if (KEY) localStorage.setItem('api_key', KEY);
let SESSION = localStorage.getItem('session_id');
if (!SESSION) { SESSION = 'ui-' + Math.random().toString(36).slice(2, 10); localStorage.setItem('session_id', SESSION); }
const chat = document.getElementById('chat');
const input = document.getElementById('msg');
const btn = document.getElementById('send');

input.addEventListener('keydown', e => { if (e.key === 'Enter' && !btn.disabled) send(); });

async function send() {
  const text = input.value.trim();
  if (!text) return;
  addMsg(text, 'user');
  input.value = '';
  btn.disabled = true;

  const progressEl = document.createElement('div');
  progressEl.className = 'progress';
  chat.appendChild(progressEl);

  try {
    const res = await fetch('/agent/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + KEY },
      body: JSON.stringify({ message: text, session_id: SESSION })
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          var eventType = line.slice(7);
        } else if (line.startsWith('data: ') && eventType) {
          if (eventType === 'ping') { eventType = null; continue; }
          const payload = JSON.parse(line.slice(6));
          if (eventType === 'progress') {
            const step = document.createElement('div');
            step.className = 'step active';
            step.textContent = payload.status;
            progressEl.appendChild(step);
            const prev = progressEl.querySelectorAll('.step.active');
            prev.forEach((s, i) => { if (i < prev.length - 1) s.classList.remove('active'); });
            chat.scrollTop = chat.scrollHeight;
          } else if (eventType === 'done') {
            progressEl.remove();
            if (payload.error) {
              addMsg(payload.response, 'error');
            } else {
              addMsg(payload.response, 'assistant');
            }
          }
          eventType = null;
        }
      }
    }
  } catch(e) {
    progressEl.remove();
    addMsg('Connection error: ' + e.message, 'error');
  }
  btn.disabled = false;
  input.focus();
}

function addMsg(text, cls) {
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}
</script>
</body></html>"""


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
    session_id = body.get("session_id", "default")

    # Save user message and embed for long-term memory
    msg_id = save_message(session_id, "user", message)
    remember(message, session_id, "user", msg_id)

    # Get conversation history (short-term) and semantic recall (long-term)
    history = get_history(session_id)
    memories = recall(message)
    memory_context = "\n".join(f"- {m}" for m in memories) if memories else ""

    from tools.orchestrator import orchestrate
    result = await orchestrate(history, memory_context=memory_context)

    # Save assistant response
    assistant_msg = result["response"]
    aid = save_message(session_id, "assistant", assistant_msg)
    remember(assistant_msg, session_id, "assistant", aid)

    return {**result, "session_id": session_id}


@app.post("/agent/stream")
async def agent_stream(request: Request, authorization: str = Header(None)):
    verify_key(authorization)
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", "default")

    msg_id = save_message(session_id, "user", message)
    remember(message, session_id, "user", msg_id)

    history = get_history(session_id)
    memories = recall(message)
    memory_context = "\n".join(f"- {m}" for m in memories) if memories else ""

    progress_queue = asyncio.Queue()

    async def on_progress(msg: str):
        await progress_queue.put(msg)

    from tools.orchestrator import orchestrate
    task = asyncio.create_task(orchestrate(history, memory_context=memory_context, on_progress=on_progress))

    def save_result(t):
        try:
            result = t.result()
            assistant_msg = result["response"]
            aid = save_message(session_id, "assistant", assistant_msg)
            remember(assistant_msg, session_id, "assistant", aid)
        except Exception:
            pass

    task.add_done_callback(save_result)

    async def generate():
        while not task.done():
            try:
                msg = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                yield {"event": "progress", "data": json.dumps({"status": msg})}
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": ""}
                continue

        while not progress_queue.empty():
            msg = await progress_queue.get()
            yield {"event": "progress", "data": json.dumps({"status": msg})}

        result = task.result()
        yield {"event": "done", "data": json.dumps({**result, "session_id": session_id})}

    return EventSourceResponse(generate())
