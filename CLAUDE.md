# RAD Global Sourcing Agent

Four subsystems in one repo, sharing a PostgreSQL database and a single API process:

1. **Intake agent** — scrapes federal tender opportunities from CanadaBuys (and pulls solicitation documents from SAP Ariba), extracts fields with Claude, and stages them to Postgres for human review. Runs on GitHub Actions.
2. **Review dashboard** (`index.html`) — accept/reject/assign staged tenders, then push accepted ones to CFlow. Static page on Vercel; talks to the Railway API.
3. **Generate PO** (`po.html`) — upload a contract + supplier quote PDF, Claude vision extracts both, reconciles them into a draft, renders a branded DOCX on the client letterhead template.
4. **Vendor management** (`vendors.html`) — xlsx bulk upload plus CRUD over the vendor list and RFP categories.

`po.html` and `vendors.html` are loaded as iframes inside `index.html` tabs; the parent injects `PO_API_BASE` / `VENDOR_API_BASE` so all three share one API origin.

## Operational state

**The scrape cron is paused** (PR #56, 2026-06-26). SAP Ariba began flagging the Playwright sessions as automation and blocking the account. `daily_agent.yml` has the `schedule:` block commented out — only `workflow_dispatch` remains. Don't re-enable it without addressing detection (stealth profile, slower pacing, residential proxy).

There is also a **SAP login halt guardrail**: after `dashboard_data.SAP_HALT_THRESHOLD` consecutive login failures the agent stops attempting SAP logins entirely (prevents permanent account lockout) and the dashboard shows a banner. Clear it with `python tools/clear_sap_halt.py` **after** rotating `SAP_PASSWORD` — otherwise it re-triggers on the next run.

## Commands

```bash
# Dry-run — scrape portal, print payloads, no CFlow records, no DB writes
python run.py --dry-run --limit 5

# Dry-run with weekly filters (Open + Goods + Last 7 days)
python run.py --dry-run --weekly --limit 5

# Dry-run with visible browser (debug selector issues)
python run.py --dry-run --visible --limit 1

# Scrape + record dashboard data only — skips CFlow entirely (what the cron runs)
python run.py --scrape-only

# Full run: DB mode if DATABASE_URL is set, else legacy direct-to-CFlow
python run.py

# Push all dashboard-accepted tenders to CFlow
python run.py --submit-accepted

# Database
python run.py --init-db          # create/upgrade schema (idempotent)
python run.py --migrate-state    # one-time JSON dedup state → Postgres
python run.py --list-associates  # associate workload table

# Discover CFlow form field names (run before first live submission)
python run.py --discover-fields

# API server — dashboard, PO, and vendor endpoints (port 8000)
python api.py

# Tests — no network, no browser, no DB
pytest tests/unit/ -q          # 312 tests, ~4 seconds
pytest tests/ -v --tb=short

# Reset dedup state and reprocess all tenders
python run.py --reset-state
```

## Verification

After any change, verify in this order:

1. **Unit tests pass:** `pytest tests/unit/ -q` — 312 passed in under 10 seconds
2. **Dry-run returns tenders:** `python run.py --dry-run --limit 3` — ≥1 tender with all 11 fields populated; no tracebacks
3. **Payload is correct:** `solicitation_no` looks like `PW-EZZ-*` or `WS-*`; `inquiry_link` is an absolute `https://canadabuys.canada.ca/...` URL
4. **Weekly mode works:** `python run.py --dry-run --weekly --limit 3` — more tenders (7-day window, Goods category)
5. **Deduplication works:** run dry-run twice — second run logs `Skipped: N`

Per-subsystem:

- **CFlow changes:** set `CFLOW_SUBMIT_NOW=false`, run `python run.py --limit 2`, check the draft records in the CFlow UI, delete them.
- **API/dashboard changes:** `python api.py`, then `curl localhost:8000/api/health` and `curl localhost:8000/api/tenders/count`.
- **PO changes:** POST a real contract + quote to `/api/po/extract`, poll `/api/po/extract/status/<job_id>` to `done`, then open the rendered DOCX in Word — page breaks and the signature block are the parts that regress.
- **Vendor changes:** upload `tests/fixtures/vendors_mini.xlsx` through the Vendors tab; confirm the merge doesn't duplicate existing companies.

## Common Mistakes

### Scraper

- **Don't** use `requests` or `httpx` to fetch CanadaBuys pages directly.
  **Do** always use Playwright — the portal is JS-rendered and blocks raw HTTP scrapers via robots.txt.

- **Don't** use headless Chromium without a user-agent override — CanadaBuys returns 403 for default Playwright user-agents.
  **Do** always open pages via `self._context.new_page()` (the browser context has the Chrome user-agent set).

- **Don't** reuse the CanadaBuys browser context for SAP downloads — its cookies and cache-busting headers break SAP's SPA rendering.
  **Do** create a fresh context via `scraper._browser.new_context(...)` and close it in a `finally`.

- **Don't** use `page.goto()` for pagination — relative query strings like `?page=1` break with `urljoin`.
  **Do** use `next_btn.click()` and let the browser resolve the URL natively.

- **Don't** call `_clean()` on the full body text before regex extraction — it collapses newlines, breaking `[^\n]+` patterns.
  **Do** use `.strip()` on the raw `inner_text()` and let `_capture()` clean individual matches.

- **Don't** assume `scraper._extract_detail()` returns all 11 fields — contact info is sometimes absent on the portal.
  **Do** always use `.get("field", "")` when merging detail into the tender dict.

- **Don't** process every SAP-platform tender — CanadaBuys federates provincial and municipal Ariba tenants too, and our credentials won't authenticate against them.
  **Do** resolve the SAP link and skip any host that doesn't end in `bn.cloud.ariba.com`.

### Agent loop & state

- **Don't** call `state.save()` once per tender in the CFlow path, and **don't** mark a solicitation processed before the CFlow POST succeeds — an unconfirmed tender must retry next run.
  **Do** save after the batch, and call `state.mark_processed()` only on a `200`/`201`. (`--scrape-only` is the exception: it checkpoints every 5 tenders because GitHub Actions kills long runs.)

- **Don't** raise when a single tender fails.
  **Do** catch it, log it, increment `summary.error_count`, and continue to the next tender.

- **Don't** swallow extraction problems silently — reviewers can't see logs.
  **Do** call `db.add_processing_note(tender_id, ...)`; the dashboard surfaces notes in the tender detail view.

### CFlow

- **Don't** add `None` values to the CFlow payload dict — CFlow rejects nulls with a 422.
  **Do** use `tender.get("field", "")`.

- **Don't** modify `cflow_client._build_payload()` to use display labels.
  **Do** run `python run.py --discover-fields` first — use whatever key the API returns, not what the CFlow UI shows.

### Database

- **Don't** add `CREATE EXTENSION pgcrypto` to the schema — it fails on managed Postgres free tiers.
  **Do** use built-in `gen_random_uuid()`, as `db.init_schema()` already does.

- **Don't** write a new migration path — `init_schema()` is idempotent `CREATE TABLE IF NOT EXISTS` and runs on every start.
  **Do** add columns there and keep it re-runnable.

### PO generation

- **Don't** make `/api/po/extract` do the work inline. Real POs take 3–8 minutes; Railway's HTTP edge times out at 300s and the user sees "Failed to fetch" while the server is still working.
  **Do** keep the async job pattern: `po_jobs.create()` → background thread → `202 {job_id}`, client polls `/api/po/extract/status/<job_id>`. Status returns HTTP 200 even for failed jobs so the client can tell a transport error from a job outcome.

- **Don't** treat every Anthropic `BadRequestError` as a bad PDF.
  **Do** keep the credit/quota branch in `po_extractor` — a billing failure reported as "corrupt upload" sends people hunting the wrong problem.

- **Don't** lower `ANTHROPIC_MAX_TOKENS` back to 8192 — real ~14-line contracts truncate mid-JSON and burn two retries (~5 min) before failing.
  **Do** leave it at 16384 and keep the `stop_reason == "max_tokens"` fail-fast check.

- **Don't** hand-tune DOCX layout with forced page breaks.
  **Do** use `cantSplit` on single-row tables and keep-together on the signature block — forced breaks leave large whitespace gaps on page 1.

### API & frontend

- **Don't** raise the body-size cap globally in `api.py`. Only `/api/po`, `/api/po/extract`, and `/api/vendors/upload` get `_LARGE_BODY_MAX` (30 MB); everything else stays at 64 KB so a spoofed `Content-Length` can't OOM the process.

- **Don't** hardcode the API origin in `po.html` or `vendors.html`.
  **Do** read `window.PO_API_BASE` / `window.VENDOR_API_BASE`, which the parent dashboard injects into the iframe.

- **Don't** commit a GitHub PAT. The dashboard's "trigger run" button reads the token from `localStorage`, entered by the user.

- **Don't** hardcode credentials or the CFlow workflow name in source.
  **Do** read them from environment variables via `config.py`.

## Project Structure

```
run.py                # CLI entrypoint — all flags listed under Commands
agent.py              # Orchestrator — scrape → download → extract → stage/submit
scraper.py            # Playwright/CanadaBuys — all portal interaction
sap_client.py         # SAP Ariba login + vision-guided document download
extractor.py          # Claude extraction from solicitation PDFs/DOCX
classifier.py         # Requirement classification + CSV output
cflow_client.py       # CFlow REST — _build_payload() is the field mapping
submit.py             # Bulk-submit dashboard-accepted tenders to CFlow
db.py                 # Postgres (asyncpg) — tenders, associates, pos, vendors
state.py              # Legacy JSON dedup — only used when DATABASE_URL is unset
dashboard_data.py     # data/*.json writers, XP/streak, SAP halt state
associates.py         # Round-robin assignment + workload
notifier.py           # Slack + email run summaries
config.py             # Env var loader — fails fast if required vars missing

api.py                # stdlib http.server — routes to po_routes / vendor_routes
po_extractor.py       # Claude vision on contract + quote PDFs
po_reconciler.py      # Merges contract + quote into one PO draft
po_renderer.py        # DOCX rendering on templates/po-letterhead.docx
po_jobs.py            # In-memory async job store (not durable across restarts)
po_routes.py          # /api/po/* handlers
vendor_parser.py      # xlsx → vendor rows
vendor_routes.py      # /api/vendors/* handlers

index.html            # Dashboard (Vercel) — hosts po.html + vendors.html iframes
po.html               # Generate PO tab
vendors.html          # Vendor management tab
review.html           # Standalone review page
workload.html         # Associate workload view
local_server.py       # Serves the dashboard on :8080 + /api/trigger (no PAT needed)
tools/clear_sap_halt.py

data/                 # agent_profile.json + run_history.json (committed by cron)
tests/
  unit/               # 312 tests — no network, no browser, no DB
  integration/        # currently empty
  fixtures/           # CanadaBuys HTML + vendors_mini.xlsx
.github/workflows/
  daily_agent.yml     # Cron (PAUSED) + secrets + state cache + log artifact
  ci.yml
```

## Tech Stack

- **Playwright** (not Selenium, not requests): CanadaBuys and SAP Ariba are JS-rendered and block raw HTTP
- **HTTPX** (not requests): async-native; matches the `async/await` pattern throughout
- **asyncpg + Postgres** on Railway: tenders, POs, and vendors all need querying and multi-user state. The JSON `state.py` path survives only for the legacy no-`DATABASE_URL` mode
- **stdlib `http.server`** (not FastAPI/Flask): no framework dependency; the route table in `api.py` is the whole router
- **`claude-sonnet-4-6`** for every extraction path (`po_extractor`, `extractor`, `sap_client`)
- **GitHub Actions** for the scrape, **Railway** for the API, **Vercel** for the dashboard

## Sub-agent Files

- [agents/scraper.md](agents/scraper.md) — Playwright selectors, pagination, fixture workflow
- [agents/cflow.md](agents/cflow.md) — CFlow REST API, field mapping, auth headers
- [agents/orchestrator.md](agents/orchestrator.md) — Agent loop, error handling, state lifecycle
