# Private LLM Gateway

An invite-only LLM gateway: one [OpenAI]-compatible API in front of several AI
providers, a console for the owner and partners, and a public site for
prospective partners.

## Three surfaces

| Surface | Routes | Who |
|---|---|---|
| Public site | `/`, `/models`, `/docs`, `/request-access`, `/terms`, `/privacy` | Anyone |
| Console | `/console/*` | Owner and partners (login + 2FA) |
| API | `/v1/*` | Partner servers, with a gateway key |

The public site is not a signup funnel. `/request-access` stores a pending record
for the owner to review by hand; it never creates an account, partner or key.
See `HOMEPAGE_SPEC.md`.

## Local development (no Docker)

Everything runs on one machine with SQLite and in-memory state. This is fine
for building and testing; it is **not** the production shape.

## 1. Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## 2. Configure

```powershell
Copy-Item .env.example .env
```

Then edit `.env`:

```
DATABASE_URL=sqlite+aiosqlite:///D:/projectradom/projectapicenter/data/dev.db
REDIS_URL=
COOKIE_SECURE=false
LOG_FORMAT=text
ADMIN_PASSWORD=dev-password-12345
```

Leaving `ADMIN_PASSWORD` empty generates one into `data/first-run.txt`.

## 3. Build the console

```powershell
cd frontend
npm install
npm run build
```

Vite writes the bundle to `app/static`, which FastAPI serves.

## 4. Run

```powershell
$env:PYTHONPATH="D:\projectradom\projectapicenter"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000 and sign in with the owner account.
## 5. Frontend hot reload (optional)

In a second terminal:

```powershell
cd frontend
npm run dev
```

Then use http://127.0.0.1:5173 — Vite proxies `/admin`, `/v1`, `/health` and
`/api` to the gateway on port 8000.

## 6. Tests

```powershell
$env:PYTHONPATH="D:\projectradom\projectapicenter"
.\.venv\Scripts\python.exe -m pytest tests -v
```

The suite starts a real gateway and a fake provider on free ports, and uses a
throwaway SQLite database at `data/test.db`. No real provider keys are needed
and no network calls leave the machine.
