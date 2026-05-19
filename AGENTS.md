# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

This is a crypto trading bot (ScalpBot) for Bybit perpetual futures with Telegram control. The codebase has two runnable services:

1. **Backend API** (`backend/server.py`) — FastAPI + MongoDB status/health API
2. **Trading Bot** (`main.py`) — Async trading bot requiring Bybit API keys + Telegram token

Development happens on dated feature branches (e.g. `18.05.26_ScalpBot`). The `main` branch contains a minimal subset of the code.

### Running services

**Backend API:**
```bash
source .venv/bin/activate
cd backend && uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```
Requires MongoDB running locally and a `backend/.env` with `MONGO_URL` and `DB_NAME`.

To start MongoDB (no systemd):
```bash
mongod --fork --logpath /tmp/mongod.log --dbpath /data/db
```

**Trading Bot:**
Cannot run without `BYBIT_API_KEY`, `BYBIT_API_SECRET`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` in a root `.env` file. The full bot package (mixins, engine, exchange modules) only exists on feature branches, not on `main`.

### Linting & formatting

```bash
source .venv/bin/activate
flake8 backend/server.py main.py --max-line-length=120
black --check backend/server.py main.py
isort --check-only backend/server.py main.py
```

### Testing

```bash
source .venv/bin/activate
python3 -m pytest
```

No test files exist on `main`; feature branches may include `backend/tests/`.

### Key gotchas

- MongoDB must be started manually (`mongod --fork ...`) since the VM has no systemd.
- The `bot/main.py` is an auto-generated aggregate file; the real entry point is the root `main.py`.
- Python venv is at `/workspace/.venv` — always activate before running commands.
- `requirements.txt` (root) installs PyTorch with CUDA deps (~large); `backend/requirements.txt` includes FastAPI, linters, and data libs.
