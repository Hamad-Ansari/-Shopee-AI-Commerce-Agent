# LinkedIn post — Shopee AI Commerce Agent

> Paste-ready. Fill in the two link placeholders before posting. Hook = first line
> (everything above "…see more").

---

I spent the last few weeks building something I'd actually use myself: an AI commerce agent that shops and sells on a Shopee-style marketplace — with a human approval gate on every consequential action. 🤖🛒

Meet ShopBot: a conversational agent (FastAPI + LangGraph + Ollama + Streamlit + WhatsApp) where the LLM can read and prepare, but never execute — every order and every listing waits for YOUR approval.

What it does:
• 🛒 Buyers: search, compare, order, track — each order pauses as a proposal until you approve (dashboard button or a WhatsApp Approve/Reject button)
• 🏪 Sellers: draft listings, publish after approval, stock checks, sales summaries
• 💬 WhatsApp-native: Meta webhook verification + HMAC signature checks, retry dedup, async replies
• 🧠 AI orchestration: one LangGraph thread per user, Ollama for structured intent extraction, deterministic rule-based fallback (audited)
• 🛡️ Safety by design: tool registry (READ / PREPARE / EXECUTE — the agent can never execute), approvals bound to a payload hash, persistent idempotency keys (double-approve = one order), prompt-injection defence, per-user spending limits, rate limiting, full audit trail
• 🔌 Honest integrations: mock marketplace + mock WhatsApp by default; the official Shopee Open Platform client (HMAC-SHA256 signing, seller-side APIs) is implemented but stays locked until real credentials exist — because Shopee has no Pakistan marketplace and buyer-side ordering isn't an official API. It reports honestly instead of faking success.
• 🚀 Deployment-ready: Docker + Postgres + Caddy auto-HTTPS, health/metrics endpoints, backups, 141/141 tests green

The bit I'm proudest of: the human-in-the-loop interrupt. The LangGraph thread pauses at PENDING_APPROVAL and — thanks to a persistent checkpointer — the approval survives even a server restart. I verified it live: kill the server mid-approval, restart, tap Approve → order confirmed. Exactly once.

Built phase-by-phase per a spec pack: Local MVP → Persistence & Auth → AI Orchestration → WhatsApp → Marketplace Integration → Deployment (+ a hardening pass). All 10 acceptance tests from the spec passing as pytest tests.

If you're into AI agents, human-in-the-loop design, or honest AI/marketplace integrations — I'd love your eyes on it. 👀

👉 Explore the project: [GitHub link]
👉 Try the live demo: [demo link]

What would you add to an agent like this? (Genuine question — I'm listening.)

#BuildInPublic #AIAgents #LangGraph #FastAPI #WhatsApp #ECommerce #LLMAgents #Python #HumanInTheLoop #MLOps #OpenSource #AgenticAI #PortfolioProject

---

## Shorter variant (~600 chars, if you prefer punchy)

I built an AI commerce agent where the LLM never executes anything without you. 🤖🛒

Buyers search → order → approve. Sellers draft → publish → approved. The same agent runs on WhatsApp with interactive Approve/Reject buttons. LangGraph + Ollama for orchestration, a strict approval-bound executor with idempotency keys, prompt-injection defence, and a full audit trail — 141/141 tests green, deployment-ready with Docker + Postgres + auto-HTTPS.

My favourite detail: approvals survive server restarts (persistent checkpointer). Kill the server mid-approval, tap Approve after restart → order confirmed, exactly once.

Explore it here: [GitHub link] — what would you build differently?

#BuildInPublic #AIAgents #LangGraph #FastAPI #WhatsApp #Python #OpenSource
