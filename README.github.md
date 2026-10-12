# 🤖🛒 Shopee AI Commerce Agent

> A conversational commerce agent for **buyers and sellers** — with an approval queue, a strict
> state machine, AI orchestration (LangGraph + Ollama), **WhatsApp** integration, an official
> Shopee Open Platform client, and a deployment-ready stack. **An LLM that never executes
> anything without you.**

![Phases](https://img.shields.io/badge/phases-6%2F6%20complete-success?style=flat-square)
![Tests](https://img.shields.io/badge/tests-141%20passing-brightgreen?style=flat-square)
![Acceptance](https://img.shields.io/badge/acceptance%20tests-10%2F10-blue?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11-blue?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-black?style=flat-square)
![LangGraph](https://img.shields.io/badge/LangGraph-human--in--the--loop-orange?style=flat-square)
![WhatsApp](https://img.shields.io/badge/WhatsApp-Cloud%20API-25D366?style=flat-square)

---

## ✨ What it does

| | Buyer 🛒 | Seller 🏪 | WhatsApp 💬 |
|---|---|---|---|
| **Chat** | search, compare, order, track | draft listings, stock, sales summary | the same agent, on WhatsApp |
| **Control** | every order pauses for **your approval** | publishing is approval-gated | interactive **Approve / Reject buttons** |
| **Safety** | spending limits, policy engine | validation + correction requests | signature checks, retry dedup |

- **🧠 AI orchestration** — a LangGraph state machine per user thread; Ollama extracts intents as
  structured output with a deterministic, audited rule-based fallback.
- **🛡️ Safety by design** — the agent can READ and PREPARE but never EXECUTE. Execution happens
  only through the approval-bound executor, with revalidation + persistent idempotency keys on
  every call. Full audit trail for every intent, proposal, approval, execution and error.
- **🔌 Honest integrations** — mock marketplace + mock WhatsApp by default (fully demoable);
  the **official Shopee Open Platform v2 client** (HMAC-SHA256 signing, seller-side APIs) and the
  **Meta WhatsApp Cloud client** are implemented and stay locked until real credentials exist.
- **🚀 Deployment-ready** — Docker + PostgreSQL + Caddy auto-HTTPS, rate limiting, security
  headers, `/health` + `/metrics` endpoints, backups, persistent checkpointer (paused approvals
  survive restarts).

## 🏗️ Architecture

```
 Streamlit dashboard ──┐
 WhatsApp webhook ─────┼─► FastAPI ─► LangGraph orchestrator (one thread per user)
 (Bearer API key)      │    │          ├─ extract_intent (Ollama LLM, rule fallback)
                       │    │          ├─ agent (shopping/selling, READ+PREPARE tools only)
                       │    │          └─ await_approval  ◄── human-in-the-loop interrupt
                       │    │                  └─ resume: approve/reject → executor
                       │    └─► services ─► executor (EXECUTE only, revalidate + idempotency)
                       │                 ├─► Shopee client   (mock | official)
                       │                 └─► WhatsApp client (mock | cloud, async sends)
                       └─► PostgreSQL (prod) / SQLite (dev) + Alembic migrations
```

**State machine:** `REQUESTED → VALIDATED → PENDING_APPROVAL → (REJECTED | APPROVED) →
REVALIDATED → EXECUTING → (SUCCEEDED | FAILED | UNKNOWN)` — `UNKNOWN` is resolved by a status
check, never a blind retry. Read-only actions skip approval.

## 🚀 Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                 # 141 passed
alembic upgrade head
uvicorn backend.main:app --host 0.0.0.0 --port 8000    # API (+ /docs)
streamlit run app.py --server.port 8501                # dashboard
```

Then: register in the dashboard (API key shown **once**) → chat
`"find wireless earbuds under PKR 2,000"` → `"buy the first one"` → **Approve** → order confirmed.

```bash
# Docker (dev)         docker compose up -d --build
# Docker (production)  DOMAIN=shop.example.com POSTGRES_PASSWORD=... \
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## 🧪 Testing

**141 tests, all passing** — including the spec pack's **10 acceptance tests** as pytest tests:
search within budget · proposal-before-order · price-change invalidation · double-approve
idempotency · timeout→UNKNOWN→status-check · prompt-injection defence · missing-field
correction · unauthorized-API honesty · spending-limit block · full audit trail.

## 📚 Documentation

- **[HANDOFF.md](HANDOFF.md)** — operations handoff: architecture, runbook, test inventory, open items
- **[GUIDEBOOK.html](GUIDEBOOK.html)** — the HTML5 guidebook (offline, self-contained)
- **[README.md](README.md)** — full per-phase documentation (plan → code → tests → limitations, ×6 phases)

## 🔌 Integrations & eligibility

| Integration | Status |
|---|---|
| Marketplace (mock) | ✅ default — idempotent, replay-safe, timeout simulation |
| Marketplace (official) | ✅ implemented, 🔒 locked — real Shopee Open API v2; needs an approved seller/ISV account |
| WhatsApp (mock) | ✅ default — records every outbound message |
| WhatsApp (cloud) | ✅ implemented, 🔒 locked — real Meta Graph API; needs access token + phone-number ID |
| LLM (Ollama / rules) | ✅ structured intent extraction with audited fallback |

> **Shopee reality check:** Shopee does not operate a marketplace in Pakistan, and its Open
> Platform only supports approved partners/sellers in specific markets. Buyer-side automated
> ordering is not offered by official APIs — so the buyer demo flow uses the mock client, and
> the official client reports honestly instead of faking success. No scraping, ever.

## 🗺️ Roadmap

- [ ] JWT access/refresh tokens (or OAuth) — replace single-show API keys
- [ ] Redis-backed shared rate limiter + Postgres checkpointer (multi-worker scaling)
- [ ] LLM-written replies (templates become the audited fallback)
- [ ] WhatsApp ↔ dashboard account linking
- [ ] Shopee deployment-time items: currency conversion, access-token refresh loop

## 🛠️ Stack

FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL/SQLite · Pydantic v2 · Streamlit · LangGraph
(human-in-the-loop interrupts + persistent SQLite checkpointer) · Ollama · httpx · pytest ·
Docker · Caddy (auto-HTTPS)

## 🤝 Contributing

Issues and PRs welcome — especially around the roadmap items above. Run the suite before
submitting: `python -m pytest -q`.

---

*Built phase-by-phase per a spec pack: Local MVP → Persistence & Auth → AI Orchestration →
WhatsApp → Marketplace Integration → Deployment (+ a WhatsApp hardening pass).*
