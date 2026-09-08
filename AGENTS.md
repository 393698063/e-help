# 电商帮帮 — Agent Guide

## Project Overview

An AI-powered e-commerce assistant. The backend is a LangGraph + FastAPI agent service; the frontend is a Next.js 16 web app.

```
电商帮帮/
├── AGENTS.md                  # ← this file
├── .gitignore
├── backend/
│   ├── .python-version        # Python 3.13 (outer toolchain)
│   ├── requirements_full.txt  # Reference dependency list (not yet wired to pyproject)
│   └── e-help/                # Main backend package (uv-managed)
│       ├── pyproject.toml
│       ├── .env               # API keys & DB URLs — never commit
│       └── src/e_help/
│           └── __init__.py    # Entry point: `e-help` CLI → main()
└── frontend/                  # Next.js 16 app (App Router)
    ├── package.json
    ├── next.config.ts
    └── app/
        ├── layout.tsx
        ├── page.tsx
        └── globals.css
```

---

## Backend (`backend/e-help/`)

### Stack

| Layer | Technology |
|---|---|
| Language | Python ≥ 3.14 |
| Package manager | `uv` |
| Web framework | FastAPI + Uvicorn |
| Agent framework | LangGraph + LangChain |
| LLM providers | DeepSeek (primary), OpenAI-compatible proxy, Anthropic, Qwen |
| MCP | `mcp`, `fastmcp`, `langchain-mcp-adapters` |
| Vector DB | Milvus (`pymilvus`) |
| Relational DB | PostgreSQL (`psycopg`, `SQLAlchemy`) — used for LangGraph checkpoints |
| Document parsing | MinerU API, `unstructured`, `pypdf`, `python-docx` |
| Config | `python-dotenv` + `.env` file |

### Key environment variables (`.env`)

```
DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL
CLOSE_API_KEY / CLOSE_API_BASE_URL
MINERU_API_TOKEN
MILVUS_DB_URL
DB_URL                          # PostgreSQL connection string
```

### Running locally

```bash
cd backend/e-help
uv sync                         # install dependencies from pyproject.toml
uv run e-help                   # run CLI entry point
# or: uv run uvicorn e_help.server:app --reload
```

### Adding dependencies

```bash
uv add <package>                # adds to pyproject.toml and locks
```

### Conventions

- Source lives under `src/e_help/`. Add new modules there (e.g., `src/e_help/api/`, `src/e_help/agents/`).
- Use `loguru` for logging, `pydantic` for data models, `python-dotenv` for env loading.
- FastAPI app entry: create `src/e_help/server.py` with `app = FastAPI(...)`.
- LangGraph graphs go in `src/e_help/graphs/`.
- MCP servers go in `src/e_help/mcp/`.

---

## Frontend (`frontend/my-app/`)

### Stack

| Layer | Technology |
|---|---|
| Framework | Next.js 16.3.4 (App Router) |
| Language | TypeScript 5 |
| UI | React 19, Tailwind CSS v4 |
| Linting | ESLint 9 + `eslint-config-next` |

> **Warning**: This is Next.js 16 — APIs and conventions differ significantly from Next.js 13–15. Before writing any Next.js-specific code, read `node_modules/next/dist/docs/` for current behavior.

### Running locally

```bash
cd frontend
npm install
npm run dev                     # http://localhost:3000
```

### Build & lint

```bash
npm run build
npm run lint
```

### Conventions

- All pages use the **App Router** under `app/`. No `pages/` directory.
- Path alias `@/*` maps to project root (configured in `tsconfig.json`).
- Tailwind v4: use `@import "tailwindcss"` in CSS, no `tailwind.config.js` needed.
- API calls to the backend should be proxied via Next.js route handlers in `app/api/` to avoid CORS issues in development.

---

## Development Notes

- **Do not commit `.env`** — it contains live API keys. It is listed in `.gitignore`.
- `requirements_full.txt` is a reference file. Add actual runtime deps via `uv add`, not by editing this file.
- `frontend/node_modules/` and `frontend/.next/` are gitignored.
- The project is at early scaffold stage — no business logic has been implemented yet.
