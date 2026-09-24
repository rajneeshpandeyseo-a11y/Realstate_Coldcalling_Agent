AI Real Estate Cold Calling Agent
Production-grade, low-cost AI voice agent for real-estate outbound cold calling in India (Hinglish).

Primary goal: QUALIFY LEAD → BOOK SITE VISIT, in ~2–3 minutes, at minimum cost.

Current operating mode (Phase A) - deterministic, no external LLM
LLM_PROVIDER=fixed is the default: the conversation is a fully rule-based Hinglish state machine. No Gemini call, no LLM cost - and a missing/empty GEMINI_API_KEY never breaks startup.
Real (paid) calls are hard-disabled by default: LIVE_CALLS_ENABLED=false and MOCK_MODE=true. Nothing dials out unless you explicitly flip both AND the API path is reachable.
To enable Gemini later: set LLM_PROVIDER=gemini + GEMINI_API_KEY in .env and restart. Optional, never mandatory.
Every captured requirement (property type, budget, location, purpose, timeline) is persisted to a LeadRequirement snapshot on qualification, and transcripts are downloadable (see below).
Core cost-saving principle
Deterministic conversation state machine (not LLM-driven)
LLM fallback only when rules fail (ambiguous answers, objections, recovery)
Short TTS responses, cached/reusable static phrases
Short calls (fewer telephony + STT + TTS minutes)
Provider abstraction so telephony/STT/TTS/LLM stay replaceable
Tech stack
Layer	Choice
Backend	Python 3.11 + FastAPI + WebSockets
Database	PostgreSQL + SQLAlchemy (async) + Alembic
Telephony	Plivo (as transport/audio, controls own AI)
STT	Sarvam
TTS	Sarvam Bulbul v3
LLM (fallback)	Gemini Flash-Lite
Background jobs	Arq (introduced in follow-up phase)
Testing	pytest
Containerization	Docker + Docker Compose
Project structure
ai-real-estate-calling-agent/
├── app/
│   ├── api/          # routers (health, v1 endpoints)
│   ├── core/         # dependency checks, shared infra
│   ├── db/           # engine/session (PHASE 2)
│   ├── models/       # SQLAlchemy models
│   ├── schemas/      # Pydantic schemas
│   ├── services/     # business logic
│   ├── providers/    # telephony/stt/llm/tts abstractions
│   ├── conversation/ # state machine + rule engine
│   ├── workers/      # background jobs
│   ├── config.py     # env-driven settings
│   ├── logging_config.py
│   └── main.py
├── alembic/          # migrations (PHASE 2)
├── tests/
├── scripts/
├── docs/
├── docker/
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
Development phases
✅ Project foundation
✅ PostgreSQL + Alembic
✅ Lead CRM
✅ Call lifecycle
✅ Conversation engine
✅ Provider abstractions (ABCs + registry + mocks; telephony wired into call service, LLM wired into conversation fallback)
✅ STT (Sarvam saaras:v3, codemix Hinglish mode via /speech-to-text)
✅ TTS (Sarvam Bulbul bulbul:v3, phrase audio cache for cost/latency)
✅ LLM fallback (Gemini)
✅ Telephony (Plivo provider: outbound + status mapping)
✅ Plivo webhook + WebSocket ("live agent": answer XML <Stream>, status webhook, bidirectional audio WebSocket bridging to STT -> engine -> TTS)
✅ End-to-end orchestration + simulated real call (call orchestrator: place -> answer -> live conversation -> terminate, with a per-call cost ledger; fully deterministic and cost-free in MOCK_MODE)
✅ Site visit (conversation reach closing + capture a slot, auto-create a SiteVisit + promote the lead to QUALIFIED, CRUD + REST API)
✅ Follow-up / retry (durable per-lead FollowUp records: customer callbacks and automatic retries after no-answer / busy, with a retry worker query and REST API)
✅ Cost optimization (per-component cost ledger + persisted CostRecords per call, real LLM cost capture + aggregation, deterministic-first & phrase-cache cost policy helpers, and a cost REST endpoint)
✅ Security (admin API-key auth on all management routes, webhook-token verification on telephony callbacks + media WebSocket stream, PII redaction in audit logs, request-body size limit)
✅ Docker (multi-stage non-root image, Postgres + app Compose stack, auto-applied Alembic migrations on startup, .dockerignore)
Production deployment
Monitoring + analytics
Getting started (Phase 1)
cd ai-real-estate-calling-agent
uv venv --python 3.11
uv pip install -r requirements.txt
# Optional development extras (after dependencies):
uv pip install -e ".[dev]"
copy .env.example .env         # first time only
uvicorn app.main:app --reload
Verify:

GET http://127.0.0.1:8000/health → {"status":"ok", ...}
GET http://127.0.0.1:8000/ready → dependency map (PostgreSQL shows not_configured until Phase 2)
GET http://127.0.0.1:8000/docs → interactive API docs
Database (Phase 2)
PostgreSQL runs via Docker Compose (dev database + a _test database for pytest).

docker compose up -d db                 # start PostgreSQL
uv run alembic upgrade head             # apply migrations
The Alembic config reads the sync URL from DATABASE_URL (asyncpg → psycopg2) so migrations and the app share one source of truth. 15 core tables are defined; see the app/models/ package.

Run tests
uv run pytest -v
Normal pytest is cost-free: it runs with MOCK_MODE=true and deselects the live marker (-m 'not live'). Live-provider tests never run unless you opt in with pytest -m live AND RUN_LIVE_TESTS=true.

Controlled real-call test (Phase A)
Ready for a real call TODAY with dummy data, real Plivo, and the fixed rule conversation (no Gemini). Do this deliberately, one number at a time.

Tunnel your webhooks so Plivo can reach you (Plivo must reach a public URL). We use Cloudflare quick tunnel (ngrok is NOT used):

cloudflared tunnel --url http://localhost:8000
Set PUBLIC_BASE_URL in .env to your tunnel URL (e.g. https://xxxx.trycloudflare.com).

Confirm safety stays on (.env):

MOCK_MODE=true
LIVE_CALLS_ENABLED=false
The app refuses to place a real call unless both MOCK_MODE=false and LIVE_CALLS_ENABLED=true - so a mis-saved app restart can't accidentally dial.

Seed dummy leads with YOUR test number (never hardcoded):

.\.venv\Scripts\python.exe scripts\seed_demo_leads.py --phone +9198XXXXXXX
This only creates leads - it does NOT dial.

Prepare to go live - set in .env:

MOCK_MODE=false
LIVE_CALLS_ENABLED=true
PLIVO_AUTH_ID=...
PLIVO_AUTH_TOKEN=...
PLIVO_PHONE_NUMBER=+9180......
and fill SARVAM_API_KEY for real STT/TTS. Keep LLM_PROVIDER=fixed.

Place exactly ONE call to one of the new leads:

POST /api/v1/calls        {"lead_id": "<lead id>"}
POST /api/v1/calls/{id}/transition   {"status":"initiated"}   # -> dials Plivo
The lead must be on your actual phone before you initiate.

Watch the outcome:

POST /api/v1/calls/{id}/simulate is for the cost-free demo; a REAL call is driven by Plivo webhooks + the audio WebSocket.
GET /api/v1/calls/{id} → status; GET /api/v1/calls/{id}/events → lifecycle.
GET /api/v1/calls/{id}/transcript.txt and .../transcript.json → download the Hinglish transcript.
GET /api/v1/calls/{id}/recording → recording URL/status (available / processing / unavailable) - handled gracefully, never assumed.
GET /api/v1/calls/{id}/cost → per-component cost ledger.
GET /api/v1/site-visits → the booked visit + LeadRequirement snapshot.
After the test, turn live-calling OFF again in .env:

LIVE_CALLS_ENABLED=false
MOCK_MODE=true
No bulk campaigns, no auto-retry loops (AUTO_RETRY_ENABLED=false), and live calls never come from pytest.

Live agent (Phase 11)
Plivo integration is split into two thin webhooks and one bidirectional audio WebSocket, all of which route through the deterministic provider registry so the whole flow is testable at zero cost in MOCK_MODE.

GET/POST /api/v1/webhooks/plivo/answer — returns Plivo XML with a bidirectional <Stream> pointing at the per-call media WebSocket (derived from PUBLIC_BASE_URL). app/services/call.py appends ?call_id=... to the answer URL when placing the call so the stream routes back to the right call.
POST /api/v1/webhooks/plivo/status — maps Plivo CallStatus values to the call state machine via call_service.transition_call.
WS /api/v1/ws/calls/{call_id} — speaks the Plivo Audio Streaming protocol (start / media / stop / playAudio). Each call is handled by a LiveAgentSession (app/services/live_agent.py): audio chunks → silence (RMS) end-of-speech → STT → conversation engine → TTS → playAudio.
The live turn loop persists every utterance to the conversation transcript via ConversationService, exactly like the HTTP conversation API.

End-to-end orchestration (Phase 12)
app/services/call_orchestrator.py provides the single entry point that drives a complete outbound call through the whole stack: place the call → answer → live conversation (same LiveAgentSession the WebSocket uses) → terminate via the state machine → produce a per-call cost ledger.

POST /api/v1/calls/{call_id}/simulate — run a full simulated call and get the transcript + cost ledger.
Run a complete simulated call from the console (deterministic, cost-free, no keys/balance/KYC, no real outbound call):

.\\.venv\\Scripts\\python.exe scripts\\simulate_call.py            # interested script
.\\.venv\\Scripts\\python.exe scripts\\simulate_call.py --dnc       # do-not-call script
Going live later needs no code changes: the orchestrator and providers are selected purely by configuration, so flipping MOCK_MODE=false plus TELEPHONY_PROVIDER=plivo, STT_PROVIDER=sarvam, TTS_PROVIDER=sarvam, and LLM_PROVIDER=gemini runs the exact same code path against real providers.

Site visits (Phase 13)
The conversation now runs all the way from the pitch through qualification to closing, and when the customer agrees to a site visit it auto-books a durable SiteVisit record (app/services/site_visit.py) and promotes the lead to qualified.

The engine captures the customer's natural-language booking (e.g. "shanivaar subah") into a preferred weekday + time-of-day slot.
The call orchestrator turns that booking into a SiteVisit (REQUESTED) tied to the lead + call, with preferred_date/preferred_time/location/raw notes, and marks the qualified call's lead QUALIFIED.
REST API - /api/v1/site-visits: create, list (filter by status/lead), get, patch (reschedule / confirm -> promotes lead to QUALIFIED / cancel).
.\\.venv\\Scripts\\python.exe scripts\\simulate_call.py   # now ends qualified + books a visit
Follow-ups / retries (Phase 14)
When a call doesn't fully resolve, the system now leaves a durable, lead-scoped FollowUp (app/services/follow_up.py, CRUD in app/crud/follow_up.py):

Callback — the customer asked to be called back (CALLBACK_REQUESTED); the terminal transition_call side-effect creates a reason="callback" follow-up (optional scheduled_for / notes picked up from the call event) and sets the lead to CALLBACK_REQUESTED.
Retry — the call went unanswered or busy (NO_ANSWER / BUSY); an automatic retry is scheduled with reason="retry" / "busy" and a default scheduled_for delay of DEFAULT_RETRY_HOURS=4 hours.
A duplicate-guard refuses a second pending follow-up for the same lead+reason (DuplicateFollowUpError → HTTP 409). next_due()/list_due expose the pending, due, under-attempt-cap follow-ups a future retry worker can re-dial.

Orchestrator now ships a SCRIPT_CALLBACK demo script that ends with the customer asking to be called back tomorrow evening, routing the call to callback_requested and creating the matching follow-up.
REST API - /api/v1/follow-ups: create, list (filter by status/lead), get, patch, /{id}/complete, /{id}/cancel.
.\\.venv\\Scripts\\python.exe -m pytest tests/test_follow_up.py -q   # 8 tests
Cost optimization (Phase 15)
Every simulated call now produces an auditable, per-component cost model, and the cost policy is explicitly deterministic-first — the rule-based conversation engine and cached TTS phrases are the default, so most turns cost zero LLM and zero re-synthesis.

Per-call cost ledger (app/services/call_orchestrator.py → CostLedger): telephony (duration × per-minute rate), STT, TTS and LLM.
Real LLM cost capture: TurnResult now carries used_llm + llm_cost, propagated all the way from the engine's LLM fallback (app/conversation/engine.py) through LiveAgentSession to the orchestrator, which sums it into the ledger (previously LLM was hard-coded to 0.0).
Persistent CostRecords (app/services/cost.py + app/crud/cost.py): one row per component is written on every simulated call into the existing cost_records table, so costs stay queryable after the fact.
REST API - GET /api/v1/calls/{id}/cost: returns the persisted cost records + a per-component summary + total.
Cost policy helpers (app/services/cost.py): pure functions projected_call_cost, llm_avoidance_ratio, tts_cache_hit_ratio that project a call's budget from usage + rates and quantify the deterministic-first / phrase-cache savings.
.\\.venv\\Scripts\\python.exe -m pytest tests/test_cost.py -q   # 9 tests
Security (Phase 16)
The HTTP surface is now gated with simple, deterministic secrets that respect the cost-free/mock-developer story - they protect the transport, not the provider mocks.

Admin API key (ADMIN_API_KEY, default dev-admin-key): required on every management route (leads, calls, conversation, site-visits, follow-ups) via the X-API-Key header. Enforced router-wide through app/api/deps.py → require_admin_api_key, using constant-time comparison.
Webhook token (WEBHOOK_TOKEN): validated on the telephony callback (POST /api/v1/webhooks/plivo/status) via the X-Webhook-Token header (or ?token= query, some providers only carry a query token) and on the live-agent media WebSocket stream. When WEBHOOK_TOKEN is empty the webhooks run open for local development. /answer stays open so the provider can always reach it.
PII redaction (app/services/pii.py): redact_pii masks Indian mobile numbers and email addresses; app/services/audit.py now redacts audit details before they are persisted, so raw contact details never land in logs.
Request hardening (app/main.py): an HTTP middleware rejects request bodies larger than MAX_REQUEST_BYTES (default 1 MB) with 413, and an invalid content-length with 400.
Config (app/config.py): ADMIN_API_KEY, WEBHOOK_TOKEN, MAX_REQUEST_BYTES — production must override the dev defaults.
.\\.venv\\Scripts\\python.exe -m pytest tests/test_security.py -q   # 13 tests
Docker (Phase 17)
Containerise the whole stack (PostgreSQL + API) so it runs the same anywhere. The image installs the app from pyproject.toml/uv.lock (not requirements.txt and runs as a non-root user.

Image - docker/Dockerfile: uv-based multi-stage build. Stage 1 resolves the locked dependency tree and installs the package; stage 2 copies only the venv + app sources into a python:3.11-slim runtime and runs as appuser. Exposes 8000, has a /health healthcheck, and defaults to MOCK_MODE (no paid calls) via .env.
Entrypoint - docker/entrypoint.sh: applies pending Alembic migrations (alembic upgrade head), then starts uvicorn app.main:app.
Compose stack - docker-compose.yml: a db service (Postgres 16 with a healthcheck) and an app service that builds the image, waits for the db, and overrides DATABASE_URL to the db host. Runtime secrets come from your local .env (never committed) via env_file.
.dockerignore keeps .venv, .env, tests, caches and data out of the build context.
docker compose up --build -d          # build + start db and app
docker compose ps                      # verify both services are healthy
docker compose logs -f app            # follow the API logs
docker compose down                    # stop (add -v to drop data volume)
Public webhooks (e.g. Placeholder Plivo) need PUBLIC_BASE_URL + WEBHOOK_TOKEN/ADMIN_API_KEY set to real values in .env; the dev defaults are only for local, non-production use.

Documentation
See docs/ for architecture, setup, credentials, telephony, AI providers, database, deployment, security, compliance, cost-optimization, and troubleshooting.
