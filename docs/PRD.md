# Product Requirements Document
## CanadaBuys → CFlow Sourcing Intake Agent

**Version:** 1.0  
**Date:** March 2026  
**Status:** Draft — Awaiting Confirmation

---

## 1. Problem Statement

### The Problem

A sourcing team that monitors Canadian federal IT tender opportunities must manually run a Chrome browser plugin (Data Miner) every day to scrape the CanadaBuys portal, then manually copy extracted data into a CFlow form to initiate an internal sourcing workflow. This two-step manual process is time-consuming, error-prone, and entirely dependent on a human remembering to do it — meaning new tenders can sit unactioned for days, reducing the team's window to respond competitively.

### Who Has This Problem

**Primary user:** The internal sourcing/procurement team that monitors CanadaBuys for IT professional services opportunities (GSIN categories 153, 154, 156 — IT Professional Services, EDP, and Telecom). They are not technical users. Their job is to evaluate and bid on tenders, not manage scrapers or data pipelines.

**Secondary stakeholder:** The workflow approvers downstream in CFlow, who depend on timely and accurate intake records to begin their sourcing evaluation process.

### Current Solutions & Gaps

| Tool | Role Today | Gap |
|------|-----------|-----|
| **Data Miner (Chrome plugin)** | Scrapes tender listings from CanadaBuys into a structured format | Manual — someone must open Chrome and run it every day. Breaks silently if CanadaBuys changes its HTML. |
| **CFlow (manual form entry)** | Hosts the sourcing workflow; intake form captures all 11 tender fields | Manual — someone must copy scraped data into the form field by field. Zero automation between scrape and entry. |
| **MERX / Biddingo** | Broad tender aggregation with email alerts | Stops at email notification. No workflow initiation. Subscription cost for what is essentially a better email. |
| **Publicus.ai** | AI-assisted tender discovery and scoring | Enterprise-priced. Broad coverage adds noise. No CFlow or BPM integration. Solves discovery, not initiation. |

**Root gap across all current solutions:** Every tool stops at notification or display. None bridge discovery directly into an internal BPM system. A human is always required to translate a tender listing into a workflow record.

---

## 2. Goals & Non-Goals

### Goals (MVP)

- [ ] **G1 — Zero daily human action:** The agent runs automatically every weekday and creates CFlow sourcing requests for newly posted tenders without any manual intervention
- [ ] **G2 — Complete field extraction:** All 11 fields from the Data Miner recipe are extracted and populated in CFlow (Solicitation Title, Solicitation No, GSIN Description, Inquiry Link, Closing Date, Time & Zone, Notifications, Client, Contact Name, Contact Email, Contact Phone)
- [ ] **G3 — No duplicate entries:** A solicitation number already processed is never submitted to CFlow a second time, regardless of how many times the agent runs
- [ ] **G4 — Reliable scraping:** The agent uses a real headless browser (Playwright) so that JavaScript-rendered pages and anti-bot protections do not cause missed tenders
- [ ] **G5 — Run visibility:** After each run, the team receives a Slack or email summary listing new tenders added, tenders skipped, and any errors
- [ ] **G6 — Zero-infrastructure deployment:** The agent runs on GitHub Actions at no hosting cost, triggered on a weekday morning cron schedule

### Non-Goals (Explicitly Out of Scope for MVP)

- **Multi-portal aggregation** — Agent only covers CanadaBuys. MERX, Biddingo, provincial portals are future scope. The client's existing workflow and search filters are CanadaBuys-specific.
- **AI scoring / bid-no-bid recommendations** — The agent does intake only. Evaluating whether to bid is a human decision made inside CFlow after intake.
- **Proposal drafting or document analysis** — Out of scope. The agent extracts structured metadata only; it does not read or summarize the full tender document.
- **Real-time / sub-hourly polling** — Daily cadence matches the client's operational rhythm. Real-time monitoring is over-engineering for MVP.
- **Web UI or admin dashboard** — Run logs and state are managed via GitHub Actions artifacts and a JSON file. No GUI needed for MVP.
- **Multi-client / multi-tenant support** — This is a single-client deployment. Generalising it into a product is a future consideration.
- **Automated bid submission** — The agent initiates intake in CFlow. Humans evaluate, approve, and submit bids.

---

## 3. User Stories

### Must Have (P0)

- As a **sourcing team member**, I want new CanadaBuys IT tenders to appear automatically in CFlow each morning so that I can begin evaluation without checking the portal myself
- As a **sourcing team member**, I want all 11 structured fields pre-filled in the CFlow intake form so that I never need to manually copy data from the portal
- As a **sourcing team member**, I want the agent to only submit tenders I haven't seen before so that I don't waste time reviewing duplicate entries
- As a **sourcing team manager**, I want a daily summary of what was found and what was added to CFlow so that I have full visibility into the intake pipeline

### Should Have (P1)

- As a **sourcing team member**, I want the agent to log an error clearly (and include it in the summary notification) when a CFlow submission fails so that I can manually follow up on missed tenders
- As a **developer / admin**, I want all credentials stored as environment variables (never hardcoded) so that the agent can be securely deployed and rotated without code changes
- As a **developer / admin**, I want to run a dry-run command that shows exactly what would be sent to CFlow without creating any records so that I can validate the scraper before going live
- As a **developer / admin**, I want a field discovery tool that queries CFlow and outputs the exact form field names so that the mapping can be verified without trial and error

### Nice to Have (P2 — Post-MVP)

- As a **sourcing team member**, I want the agent to also monitor MERX and provincial portals (Ontario Tenders Portal, BC Bid) so that no relevant federal or provincial IT opportunities are missed
- As a **sourcing team member**, I want tenders automatically tagged by estimated contract value or ministry so that I can filter and prioritise in CFlow
- As a **sourcing team manager**, I want a simple web dashboard showing historical intake volume, scrape success rate, and CFlow submission history so that I can report on pipeline activity
- As a **sourcing team member**, I want the agent to detect amendments to previously submitted tenders and update the corresponding CFlow record so that I'm working from current information
- As a **developer / admin**, I want the scraper's CSS selectors to self-heal using an LLM when CanadaBuys changes its HTML so that the agent doesn't require manual maintenance after portal updates

---

## 4. Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Manual scraping hours eliminated | 100% (0 manual runs per week) | Confirmed by team after 2 weeks live |
| Field completeness rate | ≥ 90% of fields populated per record | Spot-check 10 CFlow records per week for first month |
| Duplicate CFlow entries | 0 per month | Count duplicate solicitation numbers in CFlow |
| Agent run success rate | ≥ 95% of scheduled runs complete without error | GitHub Actions run history |
| Time from portal posting to CFlow record | ≤ 24 hours (same business day) | Compare CFlow record creation time vs CanadaBuys posting date |
| Team adoption | Team stops using Data Miner within 2 weeks of go-live | Confirmed by sourcing manager |

---

## 5. Assumptions & Constraints

### Assumptions

- CFlow's REST API supports programmatic record creation with field-level data (confirmed via CFlow documentation and `/api/v1/requests` endpoint)
- CanadaBuys does not require login to access public tender listings (confirmed — listings are publicly accessible)
- The client's CanadaBuys search filter URL remains stable between runs (the filter is baked into `config.py` and overridable via environment variable)
- The sourcing workflow in CFlow has already been configured with the 11 required form fields; the agent does not create or modify the workflow definition
- New tenders are uniquely identifiable by their Solicitation Number, which can be used as a deduplication key
- GitHub Actions provides sufficient compute and scheduling reliability for a daily scraping task

### Constraints

- **Technical:** Python 3.12+, Playwright (Chromium), HTTPX, GitHub Actions. No database dependency — state managed via JSON file.
- **Scraping:** CanadaBuys blocks raw HTTP scrapers via `robots.txt`; Playwright headless browser is mandatory
- **CFlow field mapping:** Exact CFlow form field API names must be manually verified against the live workflow using the `discover_fields.py` tool before go-live
- **Scheduling:** GitHub Actions cron has ±10 minute variance on scheduled runs; this is acceptable for a daily cadence
- **Cost:** Infrastructure budget is $0 — GitHub Actions free tier (2,000 minutes/month) is more than sufficient for a ~5 minute daily run
- **Maintenance:** If CanadaBuys restructures its HTML, CSS selectors in `scraper.py` will need updating. Estimated: 1–2 hours of developer time per incident.

---

## 6. Timeline Estimate

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| **Setup & field mapping** | 0.5 days | `.env` configured, `discover_fields.py` run, CFlow field names confirmed in `cflow_client.py` |
| **Dry-run validation** | 0.5 days | `test_run.py` confirms all 11 fields scraped correctly; payloads reviewed by sourcing team |
| **CFlow integration test** | 0.5 days | 2–3 test records created in CFlow staging/sandbox, fields verified by sourcing team |
| **Notification setup** | 0.5 days | Slack webhook or SMTP configured; test notification sent |
| **GitHub Actions deployment** | 0.5 days | Secrets added, workflow enabled, first scheduled run confirmed |
| **2-week parallel run** | 10 business days | Agent runs alongside manual Data Miner process; team confirms parity |
| **Hand-off & Data Miner retirement** | 0.5 days | Team confirms agent is sole intake mechanism; Data Miner plugin retired |
| **Total** | **~3 weeks** | Production agent running autonomously |

---

## 7. Current Solutions & Gaps (from Competitive Research)

The tender monitoring market is dominated by discovery tools — MERX, Biddingo, Publicus, TendersOnTime — that solve the "find the tender" problem but leave the "action the tender" problem entirely to humans. None of these platforms have a native integration with CFlow or any equivalent BPM system. The closest analogue to what this agent does would be a custom Zapier workflow connecting a tender portal to CFlow, but no such pre-built connector exists for CanadaBuys.

**The agent's competitive position:** It is not a discovery product competing with MERX or Publicus. It is a workflow automation product that assumes the client already knows what to monitor (CanadaBuys, IT categories) and automates the last mile — from portal listing to workflow record — which no current tool addresses.
