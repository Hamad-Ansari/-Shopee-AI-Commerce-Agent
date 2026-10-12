# HANDOFF — Shopee AI Commerce Agent

**Status: COMPLETE.** All 6 phases of the spec pack + post-pack WhatsApp hardening are
done, tested and live-verified. **141/141 tests green**, all 10 acceptance tests
passing as pytest tests.

A conversational commerce agent (buyers + sellers) with an approval queue, a strict
state machine, a policy engine, AI orchestration (LangGraph + Ollama with a
human-in-the-loop interrupt), WhatsApp integration (Meta webhooks + interactive
approve/reject buttons), an official Shopee Open Platform client, and a
deployment-ready stack (rate limiting, security headers, health/metrics, backups,
auto-HTTPS proxy).

---

## 1. Quick start (local dev)

```bash
cd /home/user/shopee_ai_agent
pip install -r requirements.txt
cp .env.example .env            # optional; defaults work out of the box
python -m pytest -q             # 141 passed
alembic upgrade head            # migrations (init_db create_all is the dev fallback)
uvicorn backend.main:app --host 0.0.0.0 --port 8000    # API
streamlit run app.py --server.port 8501                # dashboard
```

- API: http://localhost:8000 (docs at `/docs`) · Dashboard: http://localhost:8501
- Register in the dashboard → the API key is shown **once** → paste it in the sidebar.
- Try: *"find wireless earbuds under PKR 2,000"* → *"buy the first one"* → **Approve**.
- WhatsApp: POST a Meta-shaped payload to `/webhooks/whatsapp` (see `tests/test_whatsapp.py`
  for payload shapes; the phone number is the user id).

## 2. Architecture

```
Streamlit dashboard ─┐
WhatsApp webhook ─────┼─► FastAPI ─► LangGraph orchestrator (one thread per user)
(auth: Bearer key)    │      │            ├─ extract_intent (Ollama LLM, rule fallback)
                      │      │            ├─ agent (shopping/selling, READ+PREPARE tools only)
                      │      │            └─ await_approval (interrupt at PENDING_APPROVAL)
                      │      │                    └─ resume: approve/reject → executor
                      │      └─► services (order/seller/approval/policy/audit/user)
                      │                └─► executors ─► ShopeeClient (mock | official)
                      │                └─► WhatsAppClient (mock | cloud) [background sends]
                      └─► PostgreSQL (prod) / SQLite (dev) — Alembic migrations
```

**State machine (every consequential action):**
`REQUESTED → VALIDATED → PENDING_APPROVAL → (REJECTED | APPROVED) → REVALIDATED →
EXECUTING → (SUCCEEDED | FAILED | UNKNOWN)`; `UNKNOWN → (SUCCEEDED | FAILED)`;
read-only actions skip approval.

**Non-negotiable safety invariants (implemented, tested):**
1. The agent/LLM never touches the DB or marketplace directly — only typed tools
   from a registry with levels READ / PREPARE / EXECUTE.
2. EXECUTE tools (`execute_place_order`, `execute_publish_listing`) are callable only
   by the executor after a valid approval; the agent cannot call them.
3. Approval is bound to `proposal_id + proposal_version + user_id + payload_hash`;
   any change to price/quantity/seller/shipping invalidates it.
4. Permissions, price, stock, spending limit, marketplace rules and integration
   availability are revalidated before every execution.
5. Every EXECUTE carries an idempotency key (persistent `executions` table, unique
   constraint); on timeout the outcome is UNKNOWN → status check, never a blind retry.
6. Secrets live only in backend env/secret storage — never in prompts, logs or the
   browser (API keys shown once at register; only sha256 stored; `UserView`/`me`
   never expose hashes).
7. No scraping, no bypassing marketplace rules — `ShopeeClient` interface with
   `MockShopeeClient` (default) and `OfficialShopeeClient` (real Open Platform v2,
   unauthorized until credentials are configured).
8. Customer messages, product titles, reviews and descriptions are untrusted DATA,
   never instructions (prompt-injection defence; `product_card()` strips descriptions).
9. An audit event is recorded for every intent, proposal, approval, rejection,
   execution, error, WhatsApp in/out/button, duplicate and signature rejection.
10. Per-user spending limits, rate limits (agent-level + HTTP-level) and max
    quantity per order are enforced.
11. Real marketplace outcomes are reported; unverified success is never claimed
    (unauthorized integration → honest "local draft / stays pending" + audit).

## 3. API surface

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/auth/register` | — | create user, API key shown once |
| GET | `/auth/me` | Bearer | profile (never secrets) |
| POST | `/chat` | Bearer | orchestrator message (user_id must match token) |
| GET | `/products/search?q=` | — | catalog search (public) |
| GET | `/products/{id}` | — | product detail |
| GET | `/orders` | Bearer | my orders |
| GET | `/orders/{id}` | Bearer | order detail (owner) |
| GET | `/proposals/{id}` | Bearer | proposal detail (owner) |
| POST | `/proposals/{id}/approve` | Bearer | approve → revalidate → execute (idempotent) |
| POST | `/proposals/{id}/reject` | Bearer | reject |
| POST | `/proposals/{id}/check-status` | Bearer | resolve UNKNOWN via marketplace status |
| GET | `/audit` | Bearer | my audit events (`?event_type=&limit=`) |
| GET | `/webhooks/whatsapp` | — | Meta verification handshake |
| POST | `/webhooks/whatsapp` | HMAC sig* | inbound messages/buttons → replies (async send) |
| GET | `/` `/health/live` `/health/ready` | — | probes |
| GET | `/metrics` | — | JSON metrics snapshot |

\* `X-Hub-Signature-256` (HMAC-SHA256) enforced when `SHOPEE_WHATSAPP_APP_SECRET` is set.

## 4. Integrations & eligibility status

| Integration | Status | Notes |
| --- | --- | --- |
| Marketplace (mock) | ✅ default | in-memory, idempotent, timeout simulation |
| Marketplace (official) | ✅ implemented, **unauthorized** | Real Shopee Open API v2 (HMAC-SHA256 signing; seller-side `add_item` / `get_item_base_info` / `get_order_detail`). Eligibility: **Shopee has no Pakistan marketplace**; needs an approved seller/ISV account in a supported market. Buyer-side ordering is not offered by official APIs (buyer flow stays mock). |
| WhatsApp (mock) | ✅ default | records `sent[]` |
| WhatsApp (cloud) | ✅ implemented, **unauthorized** | Real Meta Graph API; needs access token + phone-number ID. Webhook: signature verification, retry dedup (`wa_messages`), ack-200 + async sends, interactive Approve/Reject buttons. |
| LLM (Ollama) | ✅ implemented | structured intent extraction, `format="json"`, rule-based fallback (audited as `llm_fallback`). No ollama server in the dev sandbox → fallback path is the live demo. |
| LLM (rule-based) | ✅ default in tests | deterministic extractor |

## 5. Security & reliability summary

- **Auth**: PBKDF2-hashed passwords; single-show API keys (sha256 stored); Bearer
  required on all user routes; ownership enforced (403/404); chat `user_id` must
  match the token.
- **Rate limiting**: per-principal (API key / IP) sliding window, 429 + `Retry-After`
  (in-memory per process — see open items for multi-worker).
- **Headers/CORS**: nosniff, DENY framing, no-referrer, CSP; CORS allow-list (no `*`).
- **Webhooks**: HMAC-SHA256 signature enforcement (when secret configured), Meta
  handshake verification, at-most-once dedup by wamid, invalid JSON → 400.
- **Audit**: append-only event log for every intent/proposal/approval/rejection/
  execution/error/WhatsApp event; queryable per user via `/audit`.
- **Idempotency**: persistent `executions` table (unique key) + replay guard in both
  executors + client-side replay caches; double approve → one order.
- **Prompt injection**: product descriptions never echoed; catalog card renderer
  strips descriptions; acceptance test T6 covers the "ignore all rules" product.
- **Monitoring**: `/health/live`, `/health/ready` (DB probe), `/metrics` (request
  counters, latency, rate-limit hits, 5xx + DB aggregates), access logs.
- **Resilience**: persistent LangGraph checkpointer — **paused approvals survive
  process restarts** (live-verified: approve after a full server restart executes).

## 6. Operations runbook

```bash
# Probes & metrics
curl localhost:8000/health/live      # {"status":"alive"}
curl localhost:8000/health/ready     # {"status":"ready"} (503 if DB down)
curl localhost:8000/metrics          # JSON snapshot (scrape-friendly)

# Docker — dev
 docker compose up -d --build         # postgres + backend + dashboard

# Docker — production (HTTPS via Caddy, no direct ports, backups)
 DOMAIN=shopbot.example.com POSTGRES_PASSWORD=... \
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
# then point DNS at the host; Caddy issues Let's Encrypt certs (remove
# `auto_https off` + `tls internal` from Caddyfile for public domains)

# Backups
scripts/backup_db.sh backup ./backups          # sqlite online backup | pg_dump -Fc
scripts/backup_db.sh restore ./backups/x.db    # restore (sqlite) | pg_restore
# prod: the compose `backup` sidecar pg_dumps every 24h with 7-day retention

# Migrations
alembic upgrade head                 # c78ac7a1b4fd (initial) + f3a9c21d7e45 (wa_messages)

# Logs
# access log: uvicorn output (method, path, status, latency) + rate-limit warnings
```

## 7. Testing

**141 tests, all passing** (`python -m pytest -q`), including the pack's 10
acceptance tests as pytest tests.

| File | # | Covers |
| --- | --- | --- |
| `test_acceptance_buyer.py` | 8 | T1–T6, T9, T10 (buyer acceptance) |
| `test_acceptance_seller.py` | 7 | T7, T8 + seller flows |
| `test_api.py` | 4 | HTTP end-to-end, auth, webhook payload handling |
| `test_auth.py` | 9 | register/me, hashing, 401/403, ownership |
| `test_graph.py` | 8 | LangGraph HITL: interrupt, resume approve/reject/unrelated, isolation |
| `test_llm_provider.py` | 6 | providers, fallback, Ollama via fake client |
| `test_whatsapp.py` | 15 | webhook verify, signature, inbound/buttons, ownership, session mapping |
| `test_whatsapp_hardening.py` | 9 | wamid dedup (retries), async send, purge, end-to-end |
| `test_official_shopee_client.py` | 29 | Open Platform v2: signing (known-answer), seller endpoints, errors, timeout |
| `test_security.py` | 22 | 401s, cross-user, secrets, headers, CORS, rate limits, metrics, deployment artifacts |
| `test_checkpointer.py` | 4 | paused approvals survive restarts |
| `test_state_machine.py` | 4 | legal/illegal transitions |
| `test_policy_engine.py` | 3 | spending limit, quantity, rate limit |
| `test_approval_service.py` | 2 | payload hash binding, idempotent approvals |
| `test_tools.py` | 3 | tool registry levels, permissions |
| `test_idempotency_store.py` | 4 | persistent idempotency, replay guard |
| `test_postgres_dialect.py` | 4 | PG dialect/DDL (no live PG in sandbox) |
| `test_api.py` (webhook) | incl. | Meta payload handling |

Run a single file: `python -m pytest tests/test_whatsapp.py -q`

## 8. Configuration (env, prefix `SHOPEE_`)

See `.env.example`. Key settings: `DATABASE_URL` (sqlite default | postgresql+psycopg),
`SHOPEE_CLIENT` (mock|official), `DEFAULT_SPENDING_LIMIT_PKR=50000`,
`MAX_QUANTITY_PER_ORDER=10`, `RATE_LIMIT_PER_MINUTE=30`, `CORS_ALLOW_ORIGINS`,
`CHECKPOINTER_PATH`, Shopee Open Platform credentials (`SHOPEE_PARTNER_ID/KEY`,
`SHOPEE_SHOP_ID`, `SHOPEE_ACCESS_TOKEN`, `SHOPEE_BASE_URL`, `SHOPEE_CATEGORY_ID`),
WhatsApp (`WHATSAPP_CLIENT`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
`WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_API_VERSION`),
LLM (`LLM_PROVIDER`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `LLM_FALLBACK_TO_RULES`).

## 9. Known limitations & open items (roadmap)

**Done post-pack:** WhatsApp retry dedup + ack-200/async sends.

**Open (documented, none blocking the demo):**
1. **Auth**: API keys only — no JWT/OAuth/refresh (upgrade candidate).
2. **Multi-worker scaling**: in-memory rate limiter + per-process metrics; Redis
   shared limiter + Postgres checkpointer for multi-replica (scaling candidate).
3. **LLM replies**: reply text is template-based; LLM-written replies with template
   fallback (feature candidate).
4. WhatsApp users are auto-provisioned, not linked to dashboard accounts; 24-hour
   customer-service window applies to free-form text.
5. Shopee: currency conversion (marketplace currency ≠ PKR), access-token refresh
   loop (4h expiry), weight/dimensions/images unmapped — deployment-time items once
   real credentials exist.
6. No WAF/DDoS in the compose stack (front with Cloudflare/cloud LB); no durable
   outbox for WhatsApp sends (in-process background tasks); schedule
   `purge_old_wa_messages`; Docker builds not exercised in this sandbox (compose
   files are YAML-validated + artifact-tested).
7. SQLite is the local default; production compose uses PostgreSQL (dialect-tested,
   not live-tested here — no PG server in the sandbox).

## 10. File map

```
app.py                        Streamlit dashboard
backend/main.py               FastAPI app, routers, probes, /metrics
backend/middleware.py         rate limiting + security headers + access logs
backend/monitoring.py         metrics registry + DB aggregates
backend/config.py             pydantic-settings (SHOPEE_ prefix)
backend/dependencies.py       singletons (clients, services, orchestrator, LLM, WA)
backend/api/                  auth, chat, products, orders, audit, webhooks
backend/agents/               graph (LangGraph HITL), shopping/selling agents,
                              tools registry (READ/PREPARE/EXECUTE), llm providers,
                              intents, prompts
backend/services/             order/seller (executors), approval, policy, audit,
                              user, product, whatsapp services
backend/integrations/         shopee_client (mock|official), whatsapp_client (mock|cloud)
backend/database/             models, session, Alembic migrations (2 revisions)
backend/schemas/              Pydantic models (no secrets in views)
data/mock_products.json       33-product mock catalog
tests/                        17 files, 141 tests
scripts/backup_db.sh          backup/restore (sqlite + postgres)
Dockerfile                    hardened (non-root, HEALTHCHECK)
docker-compose.yml            dev: postgres + backend + dashboard
docker-compose.prod.yml       prod overlay: Caddy HTTPS proxy, backups, no direct ports
Caddyfile                     routing + TLS
README.md                     full per-phase documentation
HANDOFF.md                    this file
```

---

*Built phase-by-phase per the spec pack; every phase report is in `README.md`.
Final state: 141/141 tests green, live-verified end-to-end (web dashboard + WhatsApp),
deployment-ready via `docker compose -f docker-compose.yml -f docker-compose.prod.yml`.*
