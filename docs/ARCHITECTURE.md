# Architecture: CanadaBuys → CFlow Sourcing Intake Agent

**Version:** 1.0  
**Date:** March 2026  
**Pattern:** Scheduled Batch Pipeline (Event-Driven, No Persistent Server)

---

## Architecture Decision Checklist

| Question | Answer | Implication |
|----------|--------|-------------|
| How many user types? | 0 interactive users — fully automated | No auth layer, no UI needed |
| Real-time needed? | No — daily cadence is sufficient | REST polling, no WebSockets |
| File uploads? | No | No object storage needed |
| Search beyond basic queries? | No | No search index |
| Background processing? | Yes — scraping + API calls are async | Native Python asyncio, no queue needed at this scale |
| AI/LLM features? | No (MVP) | No worker queue |
| Mobile app? | No | Not applicable |
| Multi-tenant? | No — single client deployment | No tenant isolation needed |
| Compliance? | Canadian gov data — publicly accessible | No PII storage beyond contact fields in CFlow (CFlow handles compliance) |
| Expected scale in 6 months? | ~50 tenders/day max | No horizontal scaling needed |

**Selected pattern: Scheduled Batch Pipeline** — a single Python process triggered by a cron scheduler, with no persistent server, no database, and no UI. This is the simplest architecture that fully satisfies all requirements.

---

## System Architecture Diagram

```mermaid
graph TB
    subgraph SCHEDULER ["⏰ Scheduler Layer"]
        CRON[GitHub Actions Cron\n0 12 MON-FRI\n= 7AM ET]
        MANUAL[Manual Trigger\nActions UI or CLI]
    end

    subgraph AGENT ["🤖 Agent Process (Python 3.12)"]
        direction TB
        ORCH[agent.py\nOrchestrator]

        subgraph SCRAPE ["Scrape Subsystem"]
            SC[scraper.py]
            PW[Playwright\nHeadless Chromium]
        end

        subgraph INTEGRATION ["Integration Subsystem"]
            CF[cflow_client.py\nHTTPX Async]
        end

        subgraph STATE ["State Subsystem"]
            ST[state.py]
            JSON[(processed_solicitations\n.json)]
        end

        subgraph NOTIFY ["Notify Subsystem"]
            NO[notifier.py]
        end

        subgraph CONFIG ["Config Subsystem"]
            CO[config.py\n+ .env / Secrets]
        end
    end

    subgraph EXTERNAL ["🌐 External Services"]
        PORTAL[(CanadaBuys\nPortal\ncanadabuys.canada.ca)]
        CFLOW_API[(CFlow\nREST API\nus.cflowapps.com)]
        SLACK[Slack\nIncoming Webhook]
        EMAIL[SMTP\nEmail Server]
    end

    subgraph ARTIFACTS ["📦 Run Artifacts"]
        LOG[agent.log\nGitHub Artifact\n30-day retention]
        CACHE[State Cache\nactions/cache]
    end

    CRON -->|triggers| ORCH
    MANUAL -->|triggers| ORCH

    CO -->|injects config| ORCH
    ORCH -->|fetch listings + details| SC
    SC -->|drives| PW
    PW -->|HTTP GET| PORTAL
    PORTAL -->|rendered HTML| PW
    PW -->|extracted dicts| SC
    SC -->|tender list| ORCH

    ORCH -->|check / mark| ST
    ST -->|read / write| JSON

    ORCH -->|POST request| CF
    CF -->|REST API call| CFLOW_API
    CFLOW_API -->|201 + request_id| CF

    ORCH -->|RunSummary| NO
    NO -->|webhook POST| SLACK
    NO -->|SMTP| EMAIL

    ORCH -->|write| LOG
    JSON -->|persist| CACHE

    style SCHEDULER fill:#2d3748,color:#fff
    style AGENT fill:#1a365d,color:#fff
    style EXTERNAL fill:#276749,color:#fff
    style ARTIFACTS fill:#44337a,color:#fff
    style SCRAPE fill:#2a4365,color:#fff
    style INTEGRATION fill:#2a4365,color:#fff
    style STATE fill:#2a4365,color:#fff
    style NOTIFY fill:#2a4365,color:#fff
    style CONFIG fill:#2a4365,color:#fff
```

---

## Subsystem Breakdown

### 1. Orchestrator (`agent.py`)

**Responsibility:** Owns the end-to-end run lifecycle — sequences all subsystems, tracks run-level metrics, handles errors without aborting the full run.

**Interfaces:**
- **Inputs:** Config object, loaded state, triggered by cron or CLI
- **Outputs:** Updated state file, run summary passed to notifier, log entries

**Technology:** Python 3.12 asyncio — `async/await` throughout so scraping and API calls don't block each other

**Key logic:**
- Iterates tender list; skips duplicates, processes new ones
- Catches per-tender errors without stopping the full batch (`try/except` per tender)
- Builds `RunSummary` dataclass incrementally, passes to notifier at end
- Calls `state.save()` only after all tenders processed — atomic-style update

**Verification:** `python agent.py` exits 0 with log line `Done. New: N | Skipped: N | Errors: N`

---

### 2. Scraper (`scraper.py`)

**Responsibility:** Operates a headless Chromium browser session, navigates CanadaBuys, paginates through all result pages, and extracts raw field data from both listing and detail pages.

**Interfaces:**
- **Inputs:** `ScraperConfig` (search URL, headless flag, timeout, max_pages)
- **Outputs:** `list[dict]` of tender field dicts

**Technology:**
- `playwright.async_api` — async Playwright Python bindings
- Chromium (installed via `playwright install chromium`)
- Used as async context manager (`async with CanadaBuysScraper(...) as scraper`)

**Key design decisions:**
- Two-step page loading: `wait_until="domcontentloaded"` then `wait_for_load_state("networkidle")` — more reliable than `networkidle` alone
- Custom Chrome user-agent on browser context — CanadaBuys blocks default headless Playwright user-agents with 403
- Link-based listing extraction — locates `<a>` tags matching tender-notice/award-notice/contract-history href patterns; regex fallback on `page.content()` if locators miss
- Click-based pagination — `next_btn.click()` instead of `goto()` to handle relative query string URLs correctly
- Cross-page dedup — `seen` set shared across all pages in a single scrape run
- Regex detail extraction on raw `inner_text()` (newlines preserved) — more resilient to markup changes than CSS selectors
- Contact tab click — detail pages use tabbed navigation; contact fields are hidden until "Contact information" tab is clicked
- Dual search modes — `DAILY_URL` (Open + Last 24h) and `WEEKLY_URL` (Open + Goods + Last 7 days); auto-detected by day of week
- `max_pages` safety cap (default: 10) prevents runaway pagination loops

**Verification:** `python run.py --dry-run --limit 3` prints populated tender dicts with all 11 fields non-empty

---

### 3. CFlow Client (`cflow_client.py`)

**Responsibility:** Maps scraped tender fields to CFlow form field names and submits via REST API.

**Interfaces:**
- **Inputs:** `CFlowConfig`, tender dict from scraper
- **Outputs:** CFlow `request_id` string on success; raises `RuntimeError` on failure

**Technology:**
- `httpx.AsyncClient` — async HTTP client with connection pooling
- JSON request/response — CFlow REST API uses `application/json`
- Auth via request headers: `api-key`, `user-key`, `username`

**Key design decisions:**
- `_build_payload()` is the only function requiring client-specific configuration — all 11 field label keys live here, making it the single touchpoint for field mapping maintenance
- `submit_immediately` flag controls whether to POST to `/api/v1/requests` (submit) or `/api/v1/requests/draft` — allows safe testing in production CFlow without polluting the live workflow
- `Source: "CanadaBuys Auto-Agent"` added as a 12th field for auditability — the sourcing team can filter CFlow records by source to distinguish agent-created from manually-created entries

**Verification:** CFlow record appears in sourcing workflow inbox with all fields populated; `source` field reads "CanadaBuys Auto-Agent"

---

### 4. State Manager (`state.py` + `processed_solicitations.json`)

**Responsibility:** Maintains a persistent record of every solicitation number that has been successfully submitted to CFlow, providing deduplication across runs.

**Interfaces:**
- **Inputs:** Solicitation number string
- **Outputs:** Boolean (already processed?); writes updated JSON to disk

**Technology:**
- Plain Python `json` module — no ORM, no database
- `pathlib.Path` for cross-platform file handling

**Data structure:**
```json
{
    "PW-EZZ-123-12345": {
        "cflow_request_id": "REQ-4821",
        "title": "IT Security Assessment Services",
        "processed_at": "2026-03-24T12:07:43Z"
    }
}
```

**Key design decisions:**
- Keyed on solicitation number when available, or `inquiry_link` URL as fallback (listing extraction doesn't provide solicitation numbers — they come from the detail page)
- Stores CFlow `request_id` for traceability — if a CFlow record is questioned, the state file maps it back to the solicitation
- `save()` called once after all tenders processed — if the run crashes mid-batch, only already-confirmed submissions are persisted; failed ones will retry next run
- Graceful corruption recovery — malformed JSON logs a warning and starts fresh (worst case: one duplicate batch)

**GitHub Actions persistence:** State file cached between runs using `actions/cache` with `restore-keys: agent-state-` prefix. On cache miss (first run or cache eviction), agent starts with empty state. At scale, if cache eviction becomes a problem, the JSON file can be committed back to the repo as a post-run step.

**Verification:** Run agent twice; second run logs `Skipped: N` for all tenders from first run, `New: 0`

---

### 5. Notifier (`notifier.py`)

**Responsibility:** Sends a structured run summary via Slack and/or email after each agent run.

**Interfaces:**
- **Inputs:** `RunSummary` dataclass (counts + tender list + error list)
- **Outputs:** Slack Block Kit message and/or HTML email; no return value

**Technology:**
- `httpx.AsyncClient` — async POST to Slack Incoming Webhook URL
- `smtplib` + `email.mime` (Python stdlib) — SMTP email with HTML body; no third-party email SDK dependency
- Both channels are independently optional — missing config = silently skipped

**Key design decisions:**
- Slack uses Block Kit (structured blocks) not plain text — gives the sourcing team clickable tender links directly in the notification
- Email uses HTML table format — readable in any mail client
- Summary always sent even if `new_count == 0` — the team should know the agent ran and found nothing, vs. the agent not running at all
- Error details included in notification body — no need to dig into GitHub Actions logs for common failures

**Verification:** Slack message received in configured channel after test run; message includes correct counts and tender titles with hyperlinks

---

### 6. Config (`config.py`)

**Responsibility:** Loads and validates all configuration from environment variables; fails fast with a clear error message if required vars are missing.

**Interfaces:**
- **Inputs:** Environment variables (from `.env` file locally, GitHub Secrets in Actions)
- **Outputs:** `Config` dataclass containing `CFlowConfig` and `ScraperConfig`

**Technology:** `python-dotenv` — loads `.env` file if present; falls back to OS environment vars

**Required variables:**

| Variable | Description |
|----------|-------------|
| `CFLOW_BASE_URL` | CFlow instance URL (e.g. `https://us.cflowapps.com`) |
| `CFLOW_API_KEY` | From Admin → Security Settings → API Settings |
| `CFLOW_USER_KEY` | From Profile → API Key |
| `CFLOW_USERNAME` | CFlow login email |
| `CFLOW_WORKFLOW_NAME` | Exact name of sourcing workflow in CFlow |

**Optional variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `CFLOW_SUBMIT_NOW` | `true` | `false` = save as draft |
| `SCRAPER_HEADLESS` | `true` | `false` = visible browser |
| `SCRAPER_URL` | (built-in URL) | Override CanadaBuys search URL |
| `NOTIFY_SLACK_WEBHOOK` | — | Slack webhook URL |
| `NOTIFY_EMAIL_TO` | — | Recipient email |
| `SMTP_HOST/PORT/USER/PASS` | — | SMTP credentials |

**Verification:** `python config.py` (or any missing var) prints: `Required environment variable 'X' is not set.`

---

## Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| **Language** | Python 3.12 | Async-native, best Playwright bindings, minimal ceremony |
| **Browser automation** | Playwright (Chromium) | Handles JS rendering, `networkidle` wait, async API, better stealth than Selenium |
| **HTTP client** | HTTPX | Async-native, cleaner API than `aiohttp`, `requests`-compatible syntax |
| **Scheduling** | GitHub Actions cron | Zero infrastructure cost, secrets management built-in, log archiving built-in |
| **State persistence** | JSON file (+ GHA cache) | No database needed; solicitation numbers are a simple set; trivially auditable |
| **Notifications** | Slack Webhooks + Python `smtplib` | No third-party SDK dependency; both are stable, free integrations |
| **Config management** | `python-dotenv` + env vars | Industry standard; same pattern works locally and in CI |
| **Target CRM/BPM** | CFlow REST API | Client's existing system; REST + JSON; well-documented |

---

## Data Flow Diagram

```mermaid
flowchart LR
    subgraph SOURCE ["Source"]
        CB[(CanadaBuys\nPublic Portal)]
    end

    subgraph TRANSFORM ["Transform (agent process)"]
        direction TB
        RAW["Raw HTML\n(Playwright)"]
        STRUCT["Structured Dict\n11 fields"]
        DEDUP{"Already\nprocessed?"}
        PAYLOAD["CFlow\nJSON Payload"]
    end

    subgraph SINK ["Sink"]
        CFLOW_REC[(CFlow\nWorkflow Record)]
        STATE_F[(State File\nJSON)]
        NOTIF[Run Summary\nSlack / Email]
    end

    CB -->|GET rendered page| RAW
    RAW -->|CSS selector extraction| STRUCT
    STRUCT -->|check sol_no or link| DEDUP
    DEDUP -->|new| PAYLOAD
    DEDUP -->|duplicate| STATE_F
    PAYLOAD -->|POST /api/v1/requests| CFLOW_REC
    CFLOW_REC -->|request_id| STATE_F
    STATE_F -->|counts| NOTIF

    style SOURCE fill:#276749,color:#fff
    style TRANSFORM fill:#1a365d,color:#fff
    style SINK fill:#44337a,color:#fff
```

---

## Deployment Architecture

```mermaid
graph TB
    subgraph GHA ["GitHub Actions (Free Tier)"]
        direction TB
        TRIGGER["Cron: 0 12 MON-FRI\nor manual dispatch"]
        RUNNER["ubuntu-latest runner\n(ephemeral VM)"]
        SECRETS["Repository Secrets\nCFLOW_* vars"]
        CACHE["actions/cache\nState file persistence"]
        ARTIFACT["actions/upload-artifact\nagent.log → 30 days"]
    end

    subgraph SETUP ["Setup Steps"]
        PY["actions/setup-python@v5\nPython 3.12"]
        PKGS["pip install -r requirements.txt\nplaywright install chromium --with-deps"]
        RESTORE["Restore state cache\nprocessed_solicitations.json"]
    end

    subgraph RUN ["Run Step"]
        AGENT["python agent.py"]
    end

    subgraph POST ["Post Steps"]
        SAVE["Save state cache"]
        UPLOAD["Upload log artifact"]
    end

    TRIGGER --> RUNNER
    SECRETS -->|injected as env vars| RUNNER
    RUNNER --> PY --> PKGS --> RESTORE --> AGENT
    AGENT --> SAVE --> UPLOAD
    CACHE <-->|restore/save| RESTORE
    CACHE <-->|restore/save| SAVE
    ARTIFACT <-- UPLOAD

    style GHA fill:#2d3748,color:#fff
    style SETUP fill:#1a365d,color:#fff
    style RUN fill:#276749,color:#fff
    style POST fill:#44337a,color:#fff
```

**Runtime per run:** ~5–8 minutes (Chromium install ~2 min on cold runner; scraping ~1–3 min depending on page count; CFlow POSTs ~1 min for 20 tenders)

**GitHub Actions free tier usage:** ~6 min/day × 22 weekdays = ~132 min/month vs. 2,000 min free tier allowance. Well within limits with 93% headroom.

---

## Security Considerations

| Concern | Approach |
|---------|---------|
| CFlow API credentials | Stored as GitHub Actions encrypted secrets; never in code or committed files |
| Slack webhook URL | Stored as GitHub Actions secret (`NOTIFY_SLACK_WEBHOOK`) |
| SMTP password | Stored as GitHub Actions secret (`SMTP_PASS`) |
| CanadaBuys data | All publicly accessible — no authentication required, no PII scraped except contact fields published by the government on public tenders |
| State file | Contains only solicitation numbers, CFlow request IDs, titles, and timestamps — no sensitive data |
| `.env` file | Listed in `.gitignore`; never committed to repo |
| Chromium sandboxing | GitHub Actions runner provides OS-level isolation; no additional sandboxing needed |

---

## Future Architecture Evolution

| Trigger | Evolution |
|---------|-----------|
| Adding MERX / provincial portals | Extract `CanadaBuysScraper` behind a `BaseScraper` interface; add `MERXScraper`, `OntarioTendersScraper` as implementations; orchestrator iterates scraper registry |
| Amendment detection | Add `amendment_no` field to state file; if scraper detects changed amendment count for known solicitation, issue a PATCH to CFlow instead of POST |
| Multi-client deployment | Parameterise config by tenant; GitHub Actions matrix strategy runs one job per client with isolated secrets |
| Self-healing selectors | On `total_found=0`, agent auto-captures a screenshot, sends it to Claude API with the field list, receives updated selectors, patches `scraper.py`, and creates a GitHub PR for human review before merge |

---

## Phase 2 Architecture: Intelligent Intake Pipeline

Phase 2 evolves the system from a batch pipeline into a human-in-the-loop intake system with persistent storage, LLM extraction, and a review dashboard.

### System Architecture (Phase 2)

```mermaid
graph TB
    subgraph "Scheduler (GitHub Actions)"
        CRON[Weekday 7AM ET Cron]
    end

    subgraph "Agent Process (Phase 1 — Automated)"
        SCRAPER[CanadaBuys Scraper<br/>Playwright]
        SAP[SAP Auto-Login<br/>Playwright]
        DOWNLOAD[Solicitation Download]
        LLM[Claude API<br/>Document Extraction]
        CLASSIFY[Multi/Regular<br/>Classification]
        ASSIGN[Associate<br/>Round-Robin]
    end

    subgraph "Storage (Railway)"
        PG[(PostgreSQL)]
    end

    subgraph "Dashboard (Vercel)"
        UI[Next.js Dashboard]
        API[API Routes]
    end

    subgraph "External Services"
        CB[CanadaBuys Portal]
        SAPBN[SAP Business Network]
        CFLOW[CFlow REST API]
        CLAUDE[Claude API]
    end

    CRON --> SCRAPER
    SCRAPER --> CB
    SCRAPER -->|SAP tender| SAP --> SAPBN
    SCRAPER -->|direct tender| DOWNLOAD --> CB
    SAP --> DOWNLOAD
    DOWNLOAD --> LLM --> CLAUDE
    LLM --> CLASSIFY --> ASSIGN --> PG

    UI --> API --> PG
    API -->|on accept| CFLOW
```

### New Subsystems

| Subsystem | File(s) | Responsibility |
|-----------|---------|---------------|
| **Database** | `db.py` | PostgreSQL connection, tender CRUD, associate queries |
| **SAP Downloader** | `sap_client.py` | Playwright login to SAP Business Network, solicitation download |
| **LLM Extractor** | `extractor.py` | Send PDF to Claude API, parse structured extraction response |
| **Classifier** | `classifier.py` | Determine Regular vs Multiple inquiry, generate CSV for multi-item |
| **Associate Manager** | `associates.py` | Round-robin assignment, workload tracking |
| **Dashboard API** | `dashboard/` | Next.js app on Vercel with API routes for tender review |

### Data Flow (Phase 2)

```
CanadaBuys HTML
  → Playwright extraction (scraper.py)
  → SAP login + download OR direct download (sap_client.py / scraper.py)
  → PDF → Claude API extraction (extractor.py)
  → Multi/Regular classification (classifier.py)
  → Associate assignment (associates.py)
  → INSERT into PostgreSQL with status='pending_review' (db.py)
  → [Dashboard: user reviews]
  → Accept → CFlow POST + file upload (cflow_client.py)
  → Reject → UPDATE status='rejected' (db.py)
```

### Technology Stack (Phase 2 Additions)

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Database | PostgreSQL 16 on Railway | Structured queries, status workflow, associate tracking; Railway free tier sufficient |
| Dashboard | Next.js 14 on Vercel | React SSR, API routes, free tier; team already uses Vercel |
| LLM | Claude API (Anthropic) | PDF understanding, structured extraction; same vendor as development tools |
| File storage | Railway volume or S3 | Solicitation PDFs and requirement CSVs need persistent storage beyond temp dirs |

### Environment Variables (Phase 2 Additions)

```bash
# PostgreSQL (Railway)
DATABASE_URL=postgresql://user:pass@host:port/dbname

# SAP Business Network (optional — falls back to manual flag)
SAP_USERNAME=your_sap_user
SAP_PASSWORD=your_sap_password

# Claude API (for document extraction)
ANTHROPIC_API_KEY=sk-ant-...

# Dashboard
NEXT_PUBLIC_API_URL=https://your-dashboard.vercel.app/api
```
