# Shopee AI Commerce Agent — Phase 6 (Deployment)

A conversational commerce agent (buyer + seller) with an approval queue, a strict
state machine, a policy engine and a full audit log. Phase 6 makes it
**deployment-ready**: HTTP rate limiting, security headers, tightened CORS,
liveness/readiness probes + a `/metrics` snapshot, a **persistent LangGraph
checkpointer** (paused approvals now survive restarts), a hardened non-root
Dockerfile, a production compose overlay with an **automatic-HTTPS Caddy proxy**
and a **backup sidecar**, a backup/restore script, and a dedicated security test
suite. Earlier phases: mock MVP, persistence/auth/migrations, AI orchestration
(LangGraph + Ollama HITL), WhatsApp (Meta webhooks + interactive buttons), and the
official Shopee Open Platform client (gated on real credentials).

A conversational commerce agent (buyer + seller) with an approval queue, a strict
state machine, a policy engine and a full audit log. Phase 5 implements the
**official Shopee Open Platform (Open API v2) client** — real HMAC-SHA256 request
signing and the seller-side endpoints (`add_item`, `get_item_base_info`,
`get_order_detail`) — sandbox-tested with an injected transport, and gated on real
credentials. Earlier phases added WhatsApp (Meta webhooks + interactive buttons),
AI orchestration (LangGraph + Ollama with a human-in-the-loop interrupt),
authentication, a persistent idempotency store, an audit-log API, Alembic migrations
and PostgreSQL support (SQLite remains the local default; a mock catalog of 33
products and mock marketplace/WhatsApp clients keep it fully demoable).

## Quickstart

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload     # API on http://localhost:8000
streamlit run app.py                  # dashboard on http://localhost:8501 (enter your API key in the sidebar)
python -m pytest -q                   # run the test suite
```

## Try it

```bash
# 1. Register — the API key is shown exactly once
curl -s -X POST localhost:8000/auth/register -H 'Content-Type: application/json' \
  -d '{"user_id":"u1","password":"secret123","role":"buyer"}'
# -> {"user_id":"u1", ..., "api_key":"sb_..."}

AUTH='Authorization: Bearer sb_...'    # use the key from step 1

# 2. Search (buyer)
curl -s -X POST localhost:8000/chat -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"user_id":"u1","message":"Find wireless earbuds under PKR 2,000"}'

# 3. Buy -> creates a proposal, NOT an order
curl -s -X POST localhost:8000/chat -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"user_id":"u1","message":"buy the first one"}'

# 4. Approve -> revalidates, executes through the mock marketplace (idempotent)
curl -s -X POST localhost:8000/proposals/{PROPOSAL_ID}/approve -H "$AUTH"

# 5. Seller: draft a listing, then approve to publish
curl -s -X POST localhost:8000/chat -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"user_id":"s1","role":"seller","message":"list a water bottle at PKR 500 with stock 20"}'

# 6. Inspect your own audit trail
curl -s "localhost:8000/audit?limit=25" -H "$AUTH"
```

## Architecture (Phase 1)

```
Streamlit app.py ──HTTP──> FastAPI (backend/main.py)
                               │  /chat, /products, /orders, /proposals, /webhooks
                               ▼
                    Orchestrator (agents/graph.py — Phase 3: LangGraph)
                               │  rule-based intent extraction (Phase 3: LLM)
                    ┌──────────┴──────────┐
                    ▼                     ▼
             ShoppingAgent          SellingAgent
                    └──────────┬──────────┘
                               ▼
                 ToolRegistry (READ | PREPARE | EXECUTE)
                               ▼
   product_service  order_service  seller_service
   approval_service policy_engine  audit_service
                               ▼
              ShopeeClient: MockShopeeClient (default) | OfficialShopeeClient (stub)
                               ▼
                         SQLite (users, sessions, proposals,
                               approvals, orders, listings, audit_events)
```

### State machine (every consequential action)

`REQUESTED → VALIDATED → PENDING_APPROVAL → (REJECTED | APPROVED) → REVALIDATED →
EXECUTING → (SUCCEEDED | FAILED | UNKNOWN)`

On `UNKNOWN` (marketplace timeout) the executor records the outcome as unconfirmed and
resolves it with a **status check** — it never blindly resubmits.

### Safety rules enforced

- The agent can only call READ/PREPARE tools; EXECUTE tools require the executor path
  with a valid approval (`approved_by`).
- Approvals are bound to `proposal_id + proposal_version + user_id + payload_hash`;
  any change to price, quantity, seller or shipping invalidates them.
- Every EXECUTE carries an idempotency key; the mock marketplace dedupes on it.
- Spending limits, max quantity per order and per-user rate limits are enforced.
- Product titles/descriptions/reviews are untrusted DATA — never parsed as
  instructions, never echoed back.
- Every intent, proposal, approval, rejection, execution and error is audited.

## Phase 2 — Persistence, Auth & Migrations

- **Authentication** — `POST /auth/register` creates a user (PBKDF2-hashed password)
  and returns a single-show API key (only its sha256 hash is stored). All
  conversational and order endpoints require `Authorization: Bearer <api_key>`;
  `GET /auth/me` returns the profile (never secrets). Proposals and orders are
  ownership-checked (403 for other users' resources).
- **Persistent idempotency store** — new `executions` table: one row per idempotency
  key, forever. The executor checks it before every marketplace call, so a key can
  never execute twice — even across restarts or lost proposal state. Failed
  executions are not recorded, so a fresh proposal (fresh key) can retry.
- **Audit-log API** — `GET /audit?event_type=&limit=` returns your own audit trail.
- **PostgreSQL** — set `SHOPEE_DATABASE_URL=postgresql+psycopg://...` (driver:
  `psycopg[binary]`). All models compile for the PostgreSQL dialect (tested).
  `docker-compose.yml` now includes a `postgres:16` service.
- **Alembic migrations** — `backend/database/migrations/`. Run `alembic upgrade head`
  (the Docker image does this before starting uvicorn). `init_db()` (create_all)
  remains as a dev/test fallback.

## Phase 3 — AI Orchestration

- **LangGraph orchestrator** — `agents/graph.py` compiles a per-user thread:
  `START → extract_intent → agent → (await_approval | END)`, with `await_approval → END`.
  The DB session reaches nodes through a `ContextVar` (never through checkpointed
  state/config). Checkpointer: `InMemorySaver` (a persistent checkpointer is a Phase 6 item).
- **Human-in-the-loop interrupt** — when a proposal is created, the thread pauses at
  `PENDING_APPROVAL` via `langgraph.types.interrupt()`. The next user message resumes
  the thread with `Command(resume=...)`: "approve" executes through the approval-bound
  executor, "reject" cancels, anything else is processed as a fresh intent.
- **LLM provider interface** — `agents/llm.py`. `OllamaProvider` (default) does
  structured intent extraction: `format="json"` + Pydantic validation against the
  shared `Intent` schema, one retry on invalid output. `RuleBasedProvider` is the
  deterministic fallback — if the model server is unreachable or keeps failing, the
  agent falls back automatically and audits an `llm_fallback` event. A hosted LLM is
  swappable by config later. Downstream routing, tools and tests are provider-agnostic.
- Reply text stays template-based in Phase 3 (only intent extraction is LLM-driven);
  LLM-generated replies are a natural extension.

## Acceptance tests → test files

| Pack §6 item | Test file |
| --- | --- |
| 1. Budget search ≤ 3 results | `tests/test_acceptance_buyer.py::test_search_returns_at_most_3_within_budget` |
| 2. Buy creates proposal, not order | `tests/test_acceptance_buyer.py::test_buy_creates_proposal_not_order` |
| 3. Price change invalidates approval | `tests/test_acceptance_buyer.py::test_price_change_invalidates_old_approval` |
| 4. Double approve → one order | `tests/test_acceptance_buyer.py::test_approve_twice_creates_single_order` |
| 5. Timeout → UNKNOWN → status check | `tests/test_acceptance_buyer.py::test_timeout_sets_unknown_and_status_check_resolves` |
| 6. Prompt injection ignored | `tests/test_acceptance_buyer.py::test_prompt_injection_in_product_data_is_ignored` |
| 7. Listing missing price → correction | `tests/test_acceptance_seller.py::test_listing_missing_price_asks_for_correction` |
| 8. No authorised API → local draft | `tests/test_acceptance_seller.py::test_unauthorized_api_keeps_listing_as_local_draft` |
| 9. Spending limit blocked | `tests/test_acceptance_buyer.py::test_spending_limit_blocks_order` |
| 10. Audit trail | `tests/test_acceptance_buyer.py::test_every_step_leaves_audit_record`, `tests/test_acceptance_seller.py::test_seller_flow_leaves_audit_record` |

Plus unit tests for the policy engine, state machine, approval binding, tool
permissions and the HTTP API.

## Phase 4 — WhatsApp

### Plan
1. `WhatsAppClient` interface: `MockWhatsAppClient` (default, records every send) and
   `WhatsAppCloudClient` (real Meta Graph API `/{phone-number-id}/messages`, raises
   `IntegrationUnavailableError` until `whatsapp_access_token` + `whatsapp_phone_number_id`
   are configured).
2. Rewrite `backend/api/webhooks.py`: `GET /webhooks/whatsapp` answers Meta's
   verification handshake (`hub.mode`/`hub.verify_token`/`hub.challenge` — dotted
   query aliases); `POST /webhooks/whatsapp` verifies `X-Hub-Signature-256`
   (HMAC-SHA256 over the raw body with `whatsapp_app_secret`; enforced when
   configured, open in dev/mock mode), parses the JSON, and sends every outbound
   spec through the client.
3. `backend/services/whatsapp_service.py`: inbound parsing
   (`entry[].changes[].value.messages[]`; statuses and unsupported types handled
   politely), **session mapping = the E.164 phone number is the orchestrator user id**
   (auto-provisioned on first message), text → `orchestrator.handle_message`, button
   taps `approve:<proposal_id>` / `reject:<proposal_id>` → the executor with an
   **ownership check** (another phone cannot approve your proposal).
4. Approval requests are sent as **interactive specs with Approve/Reject buttons**
   (title, total, proposal id) — tapping a button resumes the paused LangGraph thread.
5. Audit: `wa_inbound`, `wa_outbound`, `wa_button`, `wa_signature_rejected`,
   `integration_unavailable`.
6. Tests + docs; live smoke of a simulated Meta payload.

### Code (per file)
- `backend/integrations/whatsapp_client.py` — `WhatsAppClient` ABC (`send_text`,
  `send_interactive_buttons`, `is_authorized`); `MockWhatsAppClient` records `sent[]`
  (`{to, type, body, buttons}`) and is never unauthorized; `WhatsAppCloudClient`
  POSTs `messages` payloads to `https://graph.facebook.com/{api_version}/{phone_number_id}/messages`
  with a Bearer token via httpx — unauthorized until credentials are configured.
- `backend/config.py` — +`whatsapp_client` (`mock` default | `cloud`),
  `whatsapp_access_token`, `whatsapp_phone_number_id`, `whatsapp_app_secret`,
  `whatsapp_api_version` (default `v21.0`).
- `backend/dependencies.py` — `get_whatsapp_client` / `set_whatsapp_client` singleton
  (mock default; cloud when `whatsapp_client=cloud`).
- `backend/services/whatsapp_service.py` — `verify_meta_signature` (HMAC-SHA256 over
  the raw body vs `X-Hub-Signature-256`, `compare_digest`); `process_webhook` parses
  inbound payloads and returns outbound specs; text → orchestrator (auto-provisioning
  the phone-number user); interactive button taps route `approve:<pid>`/`reject:<pid>`
  to the order/listing executor with ownership enforcement; `needs_approval` replies
  become interactive specs with Approve/Reject buttons; unsupported types get a
  polite text reply; statuses payloads are ignored (audited only).
- `backend/api/webhooks.py` — GET hub-verify (dotted `hub.*` aliases); POST inbound:
  signature check (403 + `wa_signature_rejected` audit on mismatch when a secret is
  configured), JSON parse (400 on invalid), `process_webhook`, send specs via the
  client (`integration_unavailable` audit + skip when the client is unauthorized),
  returns `{"status": "ok", "replies": N}`.
- `backend/services/audit_service.py` — +`wa_inbound`, `wa_outbound`, `wa_button`,
  `wa_signature_rejected`.
- `tests/test_whatsapp.py` — 14 tests (see below).

### Commands to run
```bash
cd /home/user/shopee_ai_agent
pip install -r requirements.txt
python -m pytest -q
uvicorn backend.main:app --host 0.0.0.0 --port 8000
streamlit run app.py --server.port 8501
```

### Test results
`python -m pytest -q` → **77 passed** (62 from Phases 1–3 + 15 in Phase 4:
`test_webhook_accepts_meta_payloads` plus 14 new in `tests/test_whatsapp.py`).
New Phase 4 cases, including failure cases:
- `test_meta_verify_ok_and_wrong_token` — GET verify handshake accepts the right
  token, 403 on a wrong one (this caught a real bug: Meta sends dotted `hub.*`
  query params — the endpoint uses proper `Query(alias="hub.mode")` aliases).
- `test_verify_meta_signature_pure` — HMAC matches; wrong/tampered/missing
  signatures rejected.
- `test_inbound_text_provisions_user_and_replies` — first message auto-provisions the
  phone-number user; `wa_inbound` audited.
- `test_search_and_buy_via_whatsapp` — search → text spec; buy → **interactive spec
  with Approve/Reject buttons**, proposal created, **no order before approval**.
- `test_button_approve_executes_order_once` — approve → order confirmed; a second
  approve tap is **idempotent** (1 order, `place_order_calls == 1`).
- `test_button_reject_cancels_proposal` — reject → proposal REJECTED, no order.
- `test_button_ownership_enforced` — a **different phone** tapping approve gets
  "doesn't exist or belongs to a different chat"; no execution.
- `test_unknown_button_is_handled`, `test_statuses_payload_is_ignored`,
  `test_unsupported_message_type_gets_polite_reply` — robustness.
- `test_session_mapping_same_phone_same_user`, `test_different_phones_are_different_users`
  — one WhatsApp number = one orchestrator user/thread; different numbers isolated.
- `test_webhook_endpoint_sends_via_client` — end-to-end through the mock client;
  `wa_outbound` audited.
- `test_webhook_endpoint_enforces_signature_when_secret_configured` — missing/bad
  signature → 403, valid signature → 200 (failure cases).
- `test_cloud_client_unauthorized_until_configured` — `WhatsAppCloudClient` raises
  `IntegrationUnavailableError` until credentials are set.

### WhatsApp-specific limitations
- WhatsApp users are auto-provisioned as buyers; they are **not** linked to
  registered dashboard accounts (account linking is a later phase).
- Interactive buttons require the chat to be inside Meta's 24-hour customer-service
  window for free-form text; button replies are constrained to 3 buttons of ≤20 chars.
- ~~No Meta message-id dedup~~ and ~~synchronous sending~~ — **fixed post-pack** (see
  "Post-pack hardening" below).

## Phase 5 — Marketplace integration

### Eligibility check (required by the spec before Phase 5 — findings)

Checked against the current Shopee Open Platform developer guide:

- **Shopee does not operate a marketplace in Pakistan.** Open Platform eligibility
  covers SG, MY, TH, TW, ID, VN, PH, MX, CO, CL, cross-border KR/JP/CN, and BR —
  **PK is not an eligible market**. A Lahore-based consumer account cannot connect.
- **Account types:** Individual Seller / Registered Business Seller / Third-party
  Partner Platform (ISV), each with market-specific criteria (business documents;
  mall/managed-seller status or order-volume thresholds, e.g. 30 orders in the last
  30 days for SG/ID).
- **Buyer-side automated ordering is not offered.** The v2 Order domain is
  seller-side (get_order_list, get_order_detail, cancel_order, ship_order,
  logistics). The realistic integration is seller-side — exactly what the spec's
  reality check predicted.
- **Conclusion:** the official client is fully implemented and sandbox-tested, but
  stays **unauthorized** until real credentials (partner_id + partner_key + shop_id
  + access_token from an approved seller/ISV account in a supported market) are
  configured. The buyer demo flow keeps using the mock client. No scraping, no
  bypassing marketplace rules — ever.

### Plan
1. Implement `OfficialShopeeClient` against the official Open API v2: HMAC-SHA256
   signing (base string `partner_id + api_path + timestamp + access_token + shop_id`,
   keyed with the partner key; common params incl. `sign_method=HMAC-SHA256`,
   `sign_version=V2.0`).
2. Seller-side endpoints: `publish_listing` → `POST /api/v2/product/add_item`;
   `get_listing_status` → `POST /api/v2/product/get_item_base_info`;
   `get_order_status` → `GET /api/v2/order/get_order_detail` (seller-side read of
   orders customers placed on the marketplace).
3. `place_order` (buyer-side) always raises `IntegrationUnavailableError` — official
   APIs don't offer it; reported honestly instead of faking success.
4. Injectable HTTP transport → the whole client is sandbox-testable without network
   or credentials; unauthorized until `shopee_partner_id` + `shopee_partner_key` +
   `shopee_shop_id` + `shopee_access_token` are configured.
5. Error mapping: Shopee in-band `{"error": ...}` and HTTP ≥400 → `MarketplaceError`;
   timeouts → `MarketplaceTimeoutError` (the executor records UNKNOWN, never retries).
6. Config + wiring + tests + docs; live smoke of the unauthorized path (acceptance
   test T8) through the full WhatsApp → approve → LOCAL DRAFT stack.

### Code (per file)
- `backend/integrations/shopee_client.py` — `OfficialShopeeClient` REWRITTEN (was a
  stub): `_sign` (official V2 base string), `_common_params`, `_request` (signed
  GET/POST via httpx or an injected transport; in-band + HTTP error mapping;
  timeout → `MarketplaceTimeoutError`), `publish_listing` (add_item + client-side
  replay cache — the DB Execution table remains the real idempotency guard),
  `get_listing_status` (get_item_base_info + item_status mapping),
  `get_order_status` (get_order_detail + order_status mapping),
  `place_order` (always `IntegrationUnavailableError` — buyer-side not offered).
  Class constants `LIVE_BASE_URL` / `SANDBOX_BASE_URL` and the status maps.
- `backend/config.py` — +`shopee_partner_id`, `shopee_partner_key`, `shopee_shop_id`,
  `shopee_access_token`, `shopee_base_url` (default `https://partner.shopeemobile.com`;
  sandbox `https://openplatform.sandbox.test-stable.shopee.sg`), `shopee_category_id`.
- `backend/dependencies.py` — `get_shopee_client` builds `OfficialShopeeClient` from
  settings when `shopee_client=official` (mock remains the default).
- `backend/services/order_service.py`, `backend/services/seller_service.py` —
  unauthorized-client message updated (Phase 5 exists now: points at the settings).
- `tests/test_official_shopee_client.py` — 29 tests (see below).

### Commands to run
```bash
cd /home/user/shopee_ai_agent
pip install -r requirements.txt
python -m pytest -q
# Live smoke of the unauthorized official client (acceptance test T8, end-to-end):
SHOPEE_SHOPEE_CLIENT=official uvicorn backend.main:app --host 0.0.0.0 --port 8002
# then POST a seller-listing + approve-tap Meta payload to :8002/webhooks/whatsapp
# (listing stays a LOCAL DRAFT, integration_unavailable audited)
uvicorn backend.main:app --host 0.0.0.0 --port 8000   # mock client (default demo)
streamlit run app.py --server.port 8501
```

### Test results
`python -m pytest -q` → **106 passed** (77 from Phases 1–4 + 29 in Phase 5).
New Phase 5 cases, including failure cases:
- `test_unauthorized_until_credentials` / `test_partial_credentials_still_unauthorized`
  — no/partial credentials → `IntegrationUnavailableError`, **transport never called**.
- `test_place_order_not_offered_even_when_authorized` — buyer-side ordering is not an
  official API; raises honestly instead of faking an order.
- `test_signature_known_answer` — known-answer HMAC-SHA256 signature against the
  official base-string scheme (caught a transcription error in the expected value
  during development — fixed).
- `test_common_params_shape` — signed shop-API params (partner_id, timestamp, sign,
  sign_method, sign_version, access_token, shop_id).
- `test_publish_listing_happy_path` — add_item mapping (item_name, original_price,
  normal_stock, description, item_sku), signed POST, `listing_id`/`LIVE` result.
- `test_publish_listing_includes_category_when_configured`,
  `test_publish_listing_idempotent_replay` (same key → one marketplace call),
  `test_publish_listing_api_error` (in-band `error_price` → `MarketplaceError`),
  `test_publish_listing_http_error` (HTTP 500 → `MarketplaceError`),
  `test_timeout_maps_to_marketplace_timeout` (→ UNKNOWN path, never retried),
  `test_add_item_without_item_id_is_an_error`.
- `test_get_listing_status_*` — LIVE mapping, UNLIST/BANNED/REVIEWING/DELETED mapping
  (parametrized), not-found → `None`, non-numeric id → `None`.
- `test_get_order_status_mapping` (parametrized: UNPAID→PENDING, READY_TO_SHIP/
  PROCESSED→CONFIRMED, SHIPPED→SHIPPED, COMPLETED→DELIVERED, CANCELLED/IN_CANCEL→
  CANCELLED, unknown → pass-through), `test_get_order_status_not_found` → `None`,
  `test_get_order_status_api_error` (expired access token → `MarketplaceError`).

### Marketplace-specific limitations
- **No live Shopee connection in this environment** — eligibility requires an
  approved seller/ISV account in a supported market (Shopee has no Pakistan
  marketplace); the client is sandbox-tested with a fake transport only.
- **Buyer-side ordering does not exist in official APIs** — the buyer flow is
  mock-only; `place_order` always reports `IntegrationUnavailableError`.
- **Currency**: Shopee prices are in the shop's marketplace currency (TWD/THB/BRL/
  MXN…), not PKR — a conversion layer is a deployment-time concern.
- **Access tokens expire after 4 hours** — a refresh loop via
  `/api/v2/auth/access_token/get` is a deployment-time concern (documented in code).
- `category_id` is required by Shopee for most categories — configurable via
  `SHOPEE_SHOPEE_CATEGORY_ID`; weight/dimensions/images are not mapped yet.
- Client-side replay cache is in-memory; the DB Execution table (unique
  idempotency key) is the real multi-worker guard.

## Phase 6 — Deployment (current)

### Plan
1. **Rate limiting**: per-principal (API key, else client IP) sliding-window
   middleware using `SHOPEE_RATE_LIMIT_PER_MINUTE` → 429 + `Retry-After`;
   health/metrics/docs/webhook-verify paths exempt.
2. **Security headers** on every response (nosniff, DENY framing, no-referrer, CSP)
   + one-line access logs with status and latency.
3. **Monitoring**: `GET /health/live` (liveness), `GET /health/ready` (DB
   readiness, 503 when down), `GET /metrics` (JSON snapshot: request counters,
   latency, rate-limit hits, 5xx, plus DB aggregates — proposals/orders/listings by
   status, audit events by type, executions).
4. **CORS tightened** from `*` to an allow-list (`SHOPEE_CORS_ALLOW_ORIGINS`).
5. **Persistent LangGraph checkpointer** (SqliteSaver at
   `SHOPEE_CHECKPOINTER_PATH`) — a thread paused at `PENDING_APPROVAL` survives a
   process restart and resumes on the next message. Falls back to InMemorySaver
   (with a warning) if the sqlite checkpointer is unavailable.
6. **Docker hardening**: non-root `appuser`, `HEALTHCHECK` against
   `/health/ready`, restart policies + resource limits in compose; production
   overlay with a **Caddy** reverse proxy (automatic HTTPS), backend/dashboard
   publishing **no ports** (proxy-only), secrets from the environment only, and a
   **backup sidecar** (pg_dump every 24h, 7-day retention) + `scripts/backup_db.sh`
   for manual backup/restore (SQLite online backup + PostgreSQL pg_dump).
7. **Security tests** (`tests/test_security.py`, 22 tests) + restart-survival tests
   (`tests/test_checkpointer.py`, 4 tests); docs + live smoke.

### Code (per file)
- `backend/monitoring.py` (new) — `MetricsRegistry` (thread-safe counters:
  requests by status, latency sum, rate-limit hits, 5xx, uptime) + DB aggregates;
  `get_metrics` / `reset_metrics`.
- `backend/middleware.py` (new) — `RateLimiter` (sliding window per principal),
  `RateLimitMiddleware` (429 + `Retry-After`, exempt paths, metric), and
  `ObservabilityMiddleware` (security headers, request timing → metrics, access log).
- `backend/main.py` — CORS allow-list from settings; both middlewares wired
  (rate limit outermost); new routes `/health/live`, `/health/ready` (DB probe),
  `/metrics`; version 0.6.0, phase 6.
- `backend/config.py` — +`cors_allow_origins`, `checkpointer_path`.
- `backend/agents/graph.py` — `_build_checkpointer()`: SqliteSaver (persistent,
  `setup()` once) with InMemorySaver fallback; Orchestrator uses it.
- `backend/dependencies.py` — +`set_orchestrator` (test isolation).
- `tests/conftest.py` — per-run temp checkpointer path; `fresh_env` resets the
  orchestrator singleton, deletes the checkpointer file, resets rate limiter + metrics.
- `tests/test_checkpointer.py` (new) — paused approval/rejection survive a
  simulated restart (new Orchestrator on the same file resumes and executes);
  checkpointer is the sqlite one; singleton reset works.
- `tests/test_security.py` (new) — 22 tests (see below).
- `requirements.txt` — +`langgraph-checkpoint-sqlite>=2.0`.
- `Dockerfile` — hardened: `PYTHONUNBUFFERED`, non-root `appuser` (uid 10001),
  `HEALTHCHECK` → `/health/ready`.
- `docker-compose.yml` — dev compose: `restart: unless-stopped` on all services,
  resource limits, db health gate.
- `docker-compose.prod.yml` (new) — production overlay: Caddy proxy (80/443,
  auto-HTTPS), backend/dashboard with **no published ports**, `${DOMAIN}` /
  `${POSTGRES_PASSWORD}` from the environment, `SHOPEE_*` secrets wired through,
  backup sidecar (pg_dump loop, 7-day retention), `backups`/`caddy_*` volumes.
- `Caddyfile` (new) — routes API + `/health/*` + `/metrics` + docs to the backend,
  `/dashboard/*` to Streamlit (`baseUrlPath=/dashboard`), TLS via Caddy.
- `scripts/backup_db.sh` (new, executable) — `backup`/`restore` for SQLite
  (online backup via the sqlite3 backup API) and PostgreSQL (pg_dump/pg_restore).

### Commands to run
```bash
cd /home/user/shopee_ai_agent
pip install -r requirements.txt
python -m pytest -q                                     # 132 passed
python -m py_compile backend/middleware.py backend/monitoring.py backend/main.py backend/agents/graph.py
# Dev servers (mock client):
uvicorn backend.main:app --host 0.0.0.0 --port 8000
streamlit run app.py --server.port 8501
# Docker (dev):        docker compose up -d --build
# Docker (production): DOMAIN=shopbot.example.com POSTGRES_PASSWORD=... \
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
# Backups: scripts/backup_db.sh backup ./backups | scripts/backup_db.sh restore ./backups/x.db
# Probes: curl localhost:8000/health/live | /health/ready | /metrics
```

### Test results
`python -m pytest -q` → **132 passed** (106 from Phases 1–5 + 26 in Phase 6).
New Phase 6 cases, including failure cases:
- `test_protected_endpoints_require_auth` (parametrized, 8 routes) — every
  protected route returns **401 without a token**.
- `test_cross_user_access_is_forbidden` — user B cannot read/approve user A's
  proposal (403/404), sees only their own audit events, and chat with a mismatched
  `user_id` is 403.
- `test_me_never_exposes_secrets` — `/auth/me` leaks no `api_key_hash` /
  `password_hash` / raw key / password.
- `test_credentials_stored_hashed_not_plaintext` — PBKDF2 password hash, sha256
  API-key hash (never the raw key).
- `test_security_headers_present` — nosniff / DENY / no-referrer / CSP on responses.
- `test_cors_is_allowlist_not_wildcard` — configured origin granted, foreign origin
  gets nothing, never `*`.
- `test_rate_limit_returns_429_with_retry_after` — limit 5 → five 200s then 429 +
  `Retry-After`, metric recorded.
- `test_rate_limit_is_per_principal` — user A limited, user B unaffected.
- `test_health_and_metrics_are_rate_limit_exempt` — probes/metrics never limited;
  the next non-exempt request is.
- `test_metrics_snapshot_shape`, `test_health_ready_checks_the_database`.
- `test_dockerfile_is_hardened`, `test_dev_compose_is_hardened`,
  `test_prod_compose_has_tls_proxy_and_backups`, `test_backup_script_exists_and_is_executable`
  — deployment artifacts verified (non-root + HEALTHCHECK; restart policies +
  health gates; Caddy 443 + no published ports + backup sidecar + env secrets;
  backup script handles sqlite + pg_dump).
- `test_paused_approval_survives_a_restart`, `test_paused_rejection_survives_a_restart`,
  `test_checkpointer_is_persistent_not_inmemory`, `test_singleton_reset_gives_a_fresh_orchestrator`.

Failures encountered and fixed during the phase: register calls with a 2-char
password hit the `min_length=6` validation (422, no `api_key`) — tests now use
valid passwords; the metrics assertion under-counted (`/metrics` records itself
only after responding); the prod-compose port check was rewritten with real YAML
parsing (`ports: []` is the emptied override).

### Deployment-specific limitations
- **Rate limiter is in-memory per process** — correct for one worker; multi-worker
  deployments should add a shared limiter (Redis) or load-balancer rate limiting.
- **Metrics are per-process** — scrape every worker / aggregate at the proxy.
- **Caddyfile ships with `tls internal`** (self-signed) so it works on a bare IP/host
  out of the box; for a public domain, remove `auto_https off` + `tls internal` to
  get automatic Let's Encrypt certificates.
- **SQLite is still the local default** — production compose uses PostgreSQL; the
  checkpointer file is per-instance (mount a volume for it in multi-replica setups,
  or accept that paused approvals resume on the same replica).
- **No WAF/DDoS protection** in the compose stack — front it with Cloudflare or a
  cloud load balancer for public traffic.
- Carried over: API-key auth only; template-based replies; no live Ollama/Shopee/
  WhatsApp credentials in the sandbox (all integrations degrade honestly and audited).

## Post-pack hardening — WhatsApp reliability (done after Phase 6)

Two documented Phase 4 limitations, fixed on request after the pack completed:

### Plan
1. **Meta webhook retry dedup (at-most-once processing)**: every inbound message
   carries a unique wamid; a new `wa_messages` table (Alembic migration
   `f3a9c21d7e45`) stores processed ids. The insert+commit is atomic (primary
   key) — a concurrent or retried duplicate loses the race, rolls back, and is
   skipped + audited as `wa_duplicate`. Messages without an id (status receipts)
   are never deduped. The executor's idempotency store remains the real guard for
   consequential actions.
2. **Ack 200, then send async**: inbound processing (dedup, orchestrator,
   executor) stays synchronous and fast; the actual sends move to a FastAPI
   `BackgroundTasks` job with its own DB session, so Meta gets a quick 200 and
   never retries just because a send was slow. `wa_outbound` /
   `integration_unavailable` audits happen in the background task.
3. Retention helper `purge_old_wa_messages(db, days=7)` for the dedup table.
4. 9 new tests + migration verification (upgrade/downgrade) + live smoke of a
   duplicated Meta payload.

### Code (per file)
- `backend/database/models.py` — +`WaMessage` (`wa_messages`: message_id PK,
  user_id, wa_type, created_at).
- `backend/database/migrations/versions/f3a9c21d7e45_wa_messages_dedup.py` (new).
- `backend/services/audit_service.py` — +`EVENT_WA_DUPLICATE = "wa_duplicate"`.
- `backend/services/whatsapp_service.py` — `_is_duplicate()` (atomic insert +
  commit, IntegrityError → rollback + `wa_duplicate` audit + skip), called at the
  top of `_handle_message`; `purge_old_wa_messages()` retention helper.
- `backend/api/webhooks.py` — POST now returns `{"status": "ok", "replies": N}`
  immediately and queues `_send_specs(client, specs)` as a background task (own
  `SessionLocal`, per-spec send + `wa_outbound` audit, `integration_unavailable`
  audit when the client is unauthorized).
- `tests/test_whatsapp_hardening.py` (new, 9 tests).

### Commands to run
```bash
cd /home/user/shopee_ai_agent
pip install -r requirements.txt
python -m pytest -q                       # 141 passed
alembic upgrade head                      # applies f3a9c21d7e45 (wa_messages)
uvicorn backend.main:app --host 0.0.0.0 --port 8000
# Live smoke: POST the same Meta payload twice -> replies:1 then replies:0,
# one wa_duplicate audit, exactly one outbound reply.
```

### Test results
`python -m pytest -q` → **141 passed** (132 + 9 new). New cases:
`test_same_wamid_twice_is_processed_once` (retry → skipped + `wa_duplicate`
audited, inbound audited once), `test_different_wamids_are_both_processed`,
`test_messages_without_id_are_never_deduped`, `test_statuses_payload_has_no_dedup_rows`,
`test_duplicate_button_tap_is_skipped` (retry never reaches the executor:
`place_order_calls == 1`), `test_purge_old_wa_messages`,
`test_endpoint_dedups_retries_and_sends_once` (replies 1 → 0, one send, one
`wa_outbound`), `test_endpoint_full_flow_with_dedup` (search → buy → approve →
retries of buy+tap change nothing; exactly 3 replies total),
`test_wa_messages_table_is_registered`. Migration verified with
`alembic upgrade head` / `downgrade -1` / re-upgrade (10 tables, correct columns).

### Known limitations
- Dedup is at-most-once: a crash *between* the dedup commit and processing would
  lose that one message (Meta retries would be skipped). The executor's idempotency
  store still guarantees no double execution; a user can always re-send.
- The dedup table grows until `purge_old_wa_messages` is scheduled (a cron/Phase
  item); rows are tiny.
- Background sends are in-process (lost if the worker dies mid-send) — a durable
  outbox/queue (Redis/SQS) is the multi-worker upgrade path.

## Known limitations (all phases)

- **Reply text is template-based** — only intent extraction is LLM-driven; LLM-written
  user messages are a natural extension (the runtime prompts in `agents/prompts.py`
  are ready for it).
- **InMemorySaver checkpointer** — thread state (paused approvals) is lost on restart;
  a persistent checkpointer (e.g. Postgres) is a Phase 6 deployment item. The DB
  session remains the source of truth for proposals, so nothing is lost that matters.
- **No live Ollama in the demo sandbox** — the default `ollama` provider falls back to
  the rule-based extractor (audited as `llm_fallback`). Run `ollama pull llama3.2`
  locally and it switches over automatically.
- **API-key auth only** — no OAuth/JWT/refresh tokens, no password login endpoint yet.
- **SQLite by default** — PostgreSQL is config-ready and dialect-tested, but the test
  suite verifies the PostgreSQL *dialect* (DDL compilation), not a live server.
- **Idempotency store is check-then-insert within one transaction** — safe for a
  single-process deployment; a multi-worker rollout relies on the DB unique constraint.
- **No live Shopee connection** — `OfficialShopeeClient` is implemented and
  sandbox-tested, but Shopee has no Pakistan marketplace and Open Platform
  eligibility needs an approved seller/ISV account in a supported market; the
  client stays unauthorized until real credentials are configured.
- Seller sales summary is mock-level (Phase 5 brings real sales analytics).

## Phases

1. **Local MVP** ✅ — FastAPI + Streamlit, mock catalog, buyer/seller flows,
   approval queue, state machine, unit tests.
2. **Persistence** ✅ — PostgreSQL support, authentication, approval records,
   audit log + API, persistent idempotency store, Alembic migrations.
3. **AI orchestration** ✅ — Ollama behind an LLM provider interface +
   LangGraph graph, structured intent extraction (Pydantic outputs), tool registry,
   human-in-the-loop interrupt at PENDING_APPROVAL.
4. **WhatsApp** ✅ — Meta webhook verification + HMAC signature checks,
   inbound parsing, outbound replies, phone→user session mapping, interactive
   approve/reject buttons driving the same orchestrator.
5. **Marketplace integration** ✅ — `OfficialShopeeClient` against the
   official Shopee Open Platform v2 (HMAC-SHA256 signing; seller-side add_item /
   get_item_base_info / get_order_detail), sandbox-tested, gated on real credentials.
   Eligibility check: Shopee has no Pakistan marketplace; buyer-side ordering is not
   offered by official APIs (buyer flow stays on the mock client). Official APIs only;
   never scraping.
6. **Deployment** ✅ (this phase) — rate limiting, security headers, tightened CORS,
   health/metrics endpoints, persistent checkpointer (paused approvals survive
   restarts), hardened Dockerfile, prod compose with automatic-HTTPS Caddy proxy +
   backup sidecar, backup/restore script, security test suite.
6. **Deployment** — Docker, HTTPS, rate limiting, monitoring, backups, security tests.
# -Shopee-AI-Commerce-Agent
