"""
AI Librarian — Chatbot router
Queries Pinecone vector store + Ollama LLM to answer library questions.
"""
import os
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pinecone import Pinecone

router = APIRouter(prefix="/api/chat", tags=["chatbot"])

# ─── Config from env ──────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-minilm")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX = os.getenv("PINECONE_INDEX", "library-assistant")
TOP_K = int(os.getenv("CHAT_TOP_K", "6"))

SYSTEM_PROMPT = """You are the Coventry University Kazakhstan Library AI assistant.

Your knowledge comes ONLY from the library knowledge base which contains:
- Book catalog (~1014 unique books with titles, authors, ISBNs)
- Library policies and rules (borrowing, copyright, collection development)
- APA 7th edition referencing guide
- Website information (services, resources, databases, hours, contact info)

Rules:
1. ALWAYS base your answers on the retrieved context below.
2. If the answer is not in the context, say you don't have that information and suggest contacting library@coventry.edu.kz or calling +7 (700) 317-33-33.
3. NEVER invent books, rules, events, or policies not in the context.
4. Answer in the same language the user writes in (English, Russian, or Kazakh).
5. When listing books, include author, title, and ISBN if available.
6. For policy questions, cite the specific document name.
7. Be friendly and helpful, like a real librarian.
8. Keep answers concise but complete."""

# ─── Clients (lazy init) ─────────────────────────────────────────
_pc_index = None
_http_client = None


def _get_pinecone_index():
    global _pc_index
    if _pc_index is None:
        if not PINECONE_API_KEY:
            raise RuntimeError("PINECONE_API_KEY not set in .env")
        pc = Pinecone(api_key=PINECONE_API_KEY)
        _pc_index = pc.Index(PINECONE_INDEX)
    return _pc_index


def _get_http_client():
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=60.0)
    return _http_client


# ─── Helpers ──────────────────────────────────────────────────────
async def _embed(text: str) -> list[float]:
    """Get embedding vector from Ollama."""
    client = _get_http_client()
    resp = await client.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text[:500]}
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("embedding", [])


async def _query_pinecone(embedding: list[float], top_k: int = TOP_K) -> list[dict]:
    """Query Pinecone for similar vectors."""
    index = _get_pinecone_index()
    results = index.query(
        vector=embedding,
        top_k=top_k,
        include_metadata=True
    )
    contexts = []
    for match in results.get("matches", []):
        meta = match.get("metadata", {})
        text = meta.get("text_preview", "")
        source = meta.get("source", "unknown")
        score = match.get("score", 0)
        contexts.append({
            "text": text,
            "source": source,
            "score": round(score, 3),
            "title": meta.get("title", meta.get("section", meta.get("filename", "")))
        })
    return contexts


async def _chat_ollama(question: str, contexts: list[dict], history: list[dict] = None) -> str:
    """Send question + context + history to Ollama and get response."""
    # Build context string
    context_parts = []
    for i, ctx in enumerate(contexts, 1):
        context_parts.append(f"[{i}] ({ctx['source']}) {ctx['text']}")
    context_str = "\n\n".join(context_parts)

    prompt = f"""Retrieved context from the library knowledge base:

{context_str}

---
User question: {question}

Answer based on the context above. If the context doesn't contain the answer, say so."""

    # Build messages with history
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Add last 6 messages of history (3 exchanges) for context window
    if history:
        for msg in history[-6:]:
            role = msg.get("role", "user")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": prompt})

    client = _get_http_client()
    resp = await client.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": CHAT_MODEL,
            "messages": messages,
            "stream": False
        }
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("message", {}).get("content", "Sorry, I couldn't generate a response.")


# ─── API Endpoint ─────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []  # optional chat history


class ChatResponse(BaseModel):
    reply: str
    sources: list[dict] = []


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Main chat endpoint: embed query → search Pinecone → Ollama response."""
    try:
        if not req.message.strip():
            return ChatResponse(reply="Please enter a question.", sources=[])

        # 1. Embed the user's question
        embedding = await _embed(req.message)
        if not embedding:
            return ChatResponse(
                reply="Sorry, embedding service is unavailable. Please try again later.",
                sources=[]
            )

        # 2. Search Pinecone
        contexts = await _query_pinecone(embedding)
        if not contexts:
            return ChatResponse(
                reply="I couldn't find any relevant information. Please contact library@coventry.edu.kz for help.",
                sources=[]
            )

        # 3. Generate response with Ollama
        reply = await _chat_ollama(req.message, contexts, req.history)

        # 4. Return response with sources
        sources = [{"source": c["source"], "title": c["title"], "score": c["score"]} for c in contexts]
        return ChatResponse(reply=reply, sources=sources)

    except Exception as e:
        return ChatResponse(
            reply=f"Sorry, an error occurred: {str(e)}. Please try again or contact library@coventry.edu.kz.",
            sources=[]
        )
