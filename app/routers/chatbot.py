"""
AI Librarian — Chatbot router
Queries Pinecone vector store + Ollama LLM to answer library questions.
"""
import os
import re
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
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE", "__default__")
TOP_K = int(os.getenv("CHAT_TOP_K", "6"))

SYSTEM_PROMPT = """You are the Coventry University Kazakhstan Library AI assistant.

Your knowledge comes ONLY from the library knowledge base which contains:
- Book catalog records with title, author, and classification number / shelf number
- Library policies and rules (borrowing, copyright, collection development)
- APA 7th edition referencing guide
- Website information (services, resources, databases, hours, contact info)

Rules:
1. ALWAYS base your answers on the retrieved context below.
2. If the answer is not in the context, say you don't have that information and suggest contacting library@coventry.edu.kz or calling +7 (700) 317-33-33.
3. NEVER invent books, rules, events, or policies not in the context.
4. Answer in the same language the user writes in (English, Russian, or Kazakh).
5. When listing books, include title, author, and classification number so users can find the book on library shelves.
6. For policy questions, cite the specific document name.
7. Be friendly and helpful, like a real librarian.
8. Keep answers concise but complete.
9. Do NOT mention internal source ids, context numbers, vector scores, or source names like book_catalog.
10. For book search results, use this exact readable format:
I found these books:
- Title: ...
  Author: ...
  Classification number: ...
11. If the catalog has no author, write "Author: not listed in the catalog".
12. If the user asks broadly, such as "something about data science", suggest the most relevant books directly instead of asking for more details first."""

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


def _pinecone_namespace_kwargs() -> dict:
    namespace = PINECONE_NAMESPACE.strip()
    return {"namespace": namespace} if namespace else {}


def _visible_meta(value: str) -> str:
    value = (value or "").strip()
    if value.casefold() in {"unknown", "unknown.", "none", "n/a"}:
        return ""
    return value


def _clean_reply_output(reply: str) -> str:
    reply = re.sub(r"\[\d+\]\s*\((?:book_catalog|[^)]*)\)\s*", "", reply or "")
    reply = re.sub(r"\(\s*book_catalog\s*\)", "", reply)
    reply = re.sub(r"\bContext item\s+\d+\s*:?", "", reply, flags=re.IGNORECASE)
    reply = re.sub(r"\n{3,}", "\n\n", reply)
    return reply.strip()


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
        include_metadata=True,
        **_pinecone_namespace_kwargs()
    )
    contexts = []
    for match in results.get("matches", []):
        meta = match.get("metadata", {})
        text = meta.get("text_preview", meta.get("text", ""))
        source = meta.get("source", "unknown")
        score = match.get("score", 0)
        author = _visible_meta(meta.get("author", ""))
        title = _visible_meta(meta.get("title", meta.get("section", meta.get("filename", ""))))
        classification_number = _visible_meta(meta.get("classification_number", ""))
        source_type = meta.get("source_type", "")
        if source_type == "book":
            pieces = [f"Title: {title}" if title else ""]
            pieces.append(f"Author: {author}" if author else "Author: not listed in the catalog")
            if classification_number:
                pieces.append(f"Classification number: {classification_number}")
            text = "Book catalog record. " + "; ".join(piece for piece in pieces if piece) + "."
        contexts.append({
            "text": text,
            "source": source,
            "score": round(score, 3),
            "title": title,
            "author": author,
            "classification_number": classification_number,
            "source_type": source_type,
        })
    return contexts


async def _chat_ollama(question: str, contexts: list[dict], history: list[dict] = None) -> str:
    """Send question + context + history to Ollama and get response."""
    # Build context string
    context_parts = []
    for i, ctx in enumerate(contexts, 1):
        context_parts.append(f"Context item {i}: {ctx['text']}")
    context_str = "\n\n".join(context_parts)

    prompt = f"""Retrieved context from the library knowledge base:

{context_str}

---
User question: {question}

Answer based on the context above. If the context doesn't contain the answer, say so.
Use plain text with line breaks. Do not use HTML tags. Do not expose context item numbers unless the user explicitly asks for sources."""

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
        reply = _clean_reply_output(await _chat_ollama(req.message, contexts, req.history))

        # 4. Return response with sources
        sources = [
            {
                "source": c["source"],
                "title": c["title"],
                "author": c["author"],
                "classification_number": c["classification_number"],
                "score": c["score"],
            }
            for c in contexts
        ]
        return ChatResponse(reply=reply, sources=sources)

    except Exception as e:
        return ChatResponse(
            reply=f"Sorry, an error occurred: {str(e)}. Please try again or contact library@coventry.edu.kz.",
            sources=[]
        )
