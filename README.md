# eLibra Middleware (Coventry Library)

Private middleware service between the library web/mobile interface and eLibra.
The app uses FastAPI, SQLite, Playwright RPA, and an optional AI Librarian chat
powered by Pinecone and Ollama.

## Features

- Issue books by QR/barcode through the eLibra UI.
- Create and moderate return requests.
- Log issued books and return requests in SQLite.
- Auto-login to eLibra using credentials from `.env`.
- Mobile-friendly library desk scanning UI.
- Admin pages for returns, reader search, stats, and events.
- AI Librarian chat for books, policies, website information, and referencing help.

## Requirements

- Python 3.11+
- PowerShell on Windows
- Playwright Chromium
- Optional for AI chat:
  - Pinecone account and API key
  - Ollama installed and running locally
  - Ollama models: `all-minilm` and `llama3.2`

## Windows Setup

From PowerShell:

```powershell
cd C:\Users\301\Desktop\www\library-qr
```

Create a virtual environment if it does not already exist:

```powershell
python -m venv venv
```

Activate it:

```powershell
.\venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run this once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Install dependencies:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

## Environment

Create `.env` in the project root:

```env
ELIBRA_BASE_URL=https://coventry.elibra.kz
ELIBRA_LIBRARY_ID=3
ELIBRA_CLIENTID=coventry

ELIBRA_USER_EMAIL=you@example.com
ELIBRA_PASSWORD=your_elibra_password

ADMIN_PIN=9876
DB_PATH=gateway.db
CARDCODE_PREFIX=21000000

APP_ACTIVATION_KEY=AB2025-ELIBRA-MIDDLEWARE-AIDAR-BEGOTAYEV
APP_ACTIVATION_PASSWORD=AB2025-PROJECT

# AI Librarian
PINECONE_API_KEY=your_pinecone_api_key_here
PINECONE_INDEX=library-assistant
PINECONE_NAMESPACE=__default__

OLLAMA_URL=http://localhost:11434
EMBED_MODEL=all-minilm
CHAT_MODEL=llama3.2
```

Do not commit `.env`. It contains secrets.

## AI Librarian Setup

1. Create a Pinecone API key in the Pinecone console.
2. Create a Pinecone index:

```text
Name: library-assistant
Dimensions: 384
Metric: cosine
```

3. Install Ollama from https://ollama.com/download.
4. Pull the local models:

```powershell
ollama pull all-minilm
ollama pull llama3.2
```

5. Index the library data into Pinecone:

```powershell
.\venv\Scripts\python.exe scripts\reindex_library_ai.py --apply --clear
```

Re-run the indexing command whenever the catalog, PDFs, or website templates
change and you want the AI Librarian to know about the new content.

## Run The App

Use the Windows launcher so the correct event loop policy is set before Uvicorn
starts:

```powershell
.\venv\Scripts\python.exe run_windows.py --http
```

Open:

```text
http://localhost:8000/scan
```

Admin pages use `ADMIN_PIN`, for example:

```text
http://localhost:8000/admin/returns?pin=9876
```

## Routes

- `GET /scan` - library desk scanning UI.
- `POST /submit` - issue/return form handler.
- `GET /admin/returns?pin=...` - return request moderation.
- `POST /admin/returns/{id}/approve` - approve return request.
- `POST /admin/returns/{id}/reject` - reject return request.
- `GET /admin/stats?pin=...` - issue/return statistics.
- `GET /admin/search?pin=...` - reader search via RPA.
- `GET /rpa/health` - RPA health status.
- `POST /rpa/manual-login` - manual eLibra login in a browser window.
- `POST /api/chat` - AI Librarian chat endpoint.

## Troubleshooting

If activation fails in PowerShell:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\venv\Scripts\Activate.ps1
```

If `ModuleNotFoundError: No module named 'uvicorn'` appears, use the virtual
environment Python directly:

```powershell
.\venv\Scripts\python.exe run_windows.py --http
```

If AI chat says it is temporarily unavailable:

- Confirm `PINECONE_API_KEY` is set in `.env`.
- Confirm Ollama is installed and running:

```powershell
ollama list
```

- Confirm both models exist:

```powershell
ollama pull all-minilm
ollama pull llama3.2
```

- Confirm data was indexed:

```powershell
.\venv\Scripts\python.exe scripts\reindex_library_ai.py --apply --clear
```

## SQLite

`gateway.db` is created automatically at startup and stores:

- `return_requests`
- `issued_books`

## Docker

The included Docker files are a deployment sketch. Local Windows development is
best done with `run_windows.py`.

## License / Use

This repository is private and intended only for the owner/author's use.
