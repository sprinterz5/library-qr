# Linux Deployment Plan: AI Librarian

> Migrating e-libra-middleware + AI Librarian chatbot from Windows to the university Linux server (AlmaLinux).

## Current Setup

| Component | Windows (now) | Linux (target) |
|-----------|--------------|----------------|
| FastAPI app | `python run_windows.py` | Docker container |
| Ollama | localhost:11434 | Host or Docker |
| Pinecone | Cloud (no change) | Cloud (no change) |
| Nginx | N/A | Reverse proxy (existing) |
| SSL | Self-signed | University cert (existing) |

## Key Challenges

1. **Ollama on the server** — needs to be installed separately (not inside Docker)
2. **GPU access** — university server likely has no GPU → CPU-only Ollama
3. **Network** — Docker container needs to reach Ollama on the host
4. **Memory** — llama3.2 (3B) needs ~3 GB RAM; all-minilm needs ~100 MB

---

## Step-by-Step Plan

### 1. Install Ollama on the Linux Server

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull required models
ollama pull all-minilm     # embeddings (~23 MB)
ollama pull llama3.2       # chat (~2 GB)

# Verify
ollama list
ollama run llama3.2 "hello"  # quick test
```

Ollama runs as a systemd service on port 11434:
```bash
sudo systemctl status ollama
sudo systemctl enable ollama  # auto-start on boot
```

### 2. Update `.env` for Linux

```env
# Change OLLAMA_URL to reach host from Docker container
OLLAMA_URL=http://host.docker.internal:11434

# If host.docker.internal doesn't work (older Docker):
# OLLAMA_URL=http://172.17.0.1:11434
```

### 3. Update `docker-compose.yml`

```yaml
version: '3.8'

services:
  elibra-middleware:
    build: .
    container_name: elibra-middleware
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./pw_profile:/app/pw_profile
    env_file:
      - .env
    extra_hosts:
      - "host.docker.internal:host-gateway"   # <-- ADD THIS
```

The `extra_hosts` line maps `host.docker.internal` to the host machine, allowing the Docker container to reach Ollama running on the host.

### 4. Update Dockerfile (if needed)

The current Dockerfile already works. Just ensure `pinecone` is in `requirements.txt` (already added).

No GPU passthrough needed — Ollama runs on the host, not in Docker.

### 5. Configure Ollama to Accept External Connections

By default Ollama only listens on `127.0.0.1`. Docker containers connect via the host gateway, so:

```bash
# Edit Ollama systemd service
sudo systemctl edit ollama

# Add these lines:
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
```

Then restart:
```bash
sudo systemctl restart ollama
```

### 6. Deploy & Test

```bash
# On the server, in the project directory:
git pull   # or upload files

# Rebuild Docker container
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Test the chat API
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What are the library hours?"}'

# Check logs
docker-compose logs -f
```

### 7. Nginx Configuration

The existing `nginx/library.conf` already proxies to port 8000. No changes needed unless you want to add rate limiting for the chat endpoint:

```nginx
# Optional: rate limit chat API (prevent abuse)
limit_req_zone $binary_remote_addr zone=chat:10m rate=5r/m;

location /api/chat {
    limit_req zone=chat burst=3;
    proxy_pass http://127.0.0.1:8000;
    proxy_read_timeout 120s;  # Ollama can be slow on CPU
}
```

---

## Memory & Performance Estimates (CPU-only server)

| Resource | Requirement |
|----------|------------|
| RAM for Ollama | ~3 GB (llama3.2) + ~100 MB (all-minilm) |
| RAM for Docker app | ~200 MB |
| **Total RAM needed** | **~3.5 GB minimum, 4-6 GB recommended** |
| Response time | 5-15 sec per chat message (CPU) |
| Disk for models | ~2.5 GB |

> [!WARNING]
> If the server has < 4 GB RAM, llama3.2 will be very slow or crash. Consider using `llama3.2:1b` (1B params, ~1 GB RAM) instead — change `CHAT_MODEL=llama3.2:1b` in `.env`.

---

## Re-indexing on the Server

If you need to re-index (the Pinecone data is cloud-stored, so normally not needed):

```bash
# From the n8n directory (or copy indexer.py to the server)
python indexer.py \
  --pinecone-key YOUR_KEY \
  --clear

# Note: EXCEL_PATH and PDF_DIR paths in indexer.py 
# would need updating for Linux paths
```

---

## Checklist

- [ ] SSH into the server
- [ ] Install Ollama + pull models
- [ ] Configure Ollama to listen on 0.0.0.0
- [ ] Update `.env` with `OLLAMA_URL=http://host.docker.internal:11434`
- [ ] Update `docker-compose.yml` with `extra_hosts`
- [ ] `docker-compose build && docker-compose up -d`
- [ ] Test: `curl http://localhost:8000/api/chat -d '{"message":"hello"}'`
- [ ] Check Nginx timeout settings for `/api/chat`
- [ ] Verify RAM usage: `free -h` and `docker stats`
