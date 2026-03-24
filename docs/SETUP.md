# Setup Guide: CanadaBuys → CFlow Sourcing Intake Agent

**Target audience:** Developer setting up the agent for the first time, or picking it up after a handoff.  
**Time to first successful dry-run:** ~30 minutes  
**Time to production deployment:** ~2–3 hours  

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | >= 3.12 | https://python.org or `pyenv install 3.12` |
| pip | >= 23.x | Bundled with Python 3.12 |
| Git | Any modern | https://git-scm.com |
| A CFlow account | — | Admin access required (for API key retrieval) |
| A GitHub account | — | For deployment via GitHub Actions |

**No Docker required.** No database. No Node.js. This is a pure Python project.

---

## Quick Start (Local)

### 1. Clone & Set Up Environment

```bash
git clone https://github.com/YOUR_ORG/canadabuys-cflow-agent.git
cd canadabuys-cflow-agent

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows (cmd)
# .venv\Scripts\Activate.ps1       # Windows (PowerShell)

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright's Chromium browser
playwright install chromium
```

> ⚠️ **Windows users:** Playwright works on Windows but the GitHub Actions runner uses Ubuntu. Selectors and behaviour are identical — just note that `playwright install chromium` on Windows downloads the Windows build of Chromium.

---

### 2. Configure Environment Variables

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in all required values:

#### Required Variables

| Variable | Description | Where to Find It |
|----------|-------------|-----------------|
| `CFLOW_BASE_URL` | Your CFlow instance URL | Check your browser URL when logged into CFlow — e.g. `https://us.cflowapps.com` |
| `CFLOW_API_KEY` | CFlow REST API key | CFlow → Admin → Security Settings → API Settings → copy the key |
| `CFLOW_USER_KEY` | Per-user API key | CFlow → (top right avatar) → Profile → API Key → copy |
| `CFLOW_USERNAME` | Your CFlow login email | The email you use to log into CFlow |
| `CFLOW_WORKFLOW_NAME` | Exact workflow name | CFlow → Admin → Workflows → copy the name character-for-character |

> ⚠️ `CFLOW_WORKFLOW_NAME` must match **exactly** — including capitalisation and spaces. If your workflow is called `"IT Sourcing Process"`, do not write `"it sourcing process"`.

#### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CFLOW_SUBMIT_NOW` | `true` | Set to `false` to save as **draft** instead of submitting — useful during testing |
| `SCRAPER_HEADLESS` | `true` | Set to `false` to watch the browser navigate CanadaBuys (debug mode) |
| `SCRAPER_URL` | (built-in) | Override the CanadaBuys search URL if your filters change |
| `NOTIFY_SLACK_WEBHOOK` | — | Slack Incoming Webhook URL for run summaries |
| `NOTIFY_EMAIL_TO` | — | Recipient email address |
| `NOTIFY_EMAIL_FROM` | — | Sender email address |
| `SMTP_HOST` | — | SMTP server (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | — | SMTP port (e.g. `587`) |
| `SMTP_USER` | — | SMTP username |
| `SMTP_PASS` | — | SMTP password or app password |

---

### 3. Discover CFlow Form Field Names

Before the agent can populate your CFlow form correctly, you need to verify the field names it will use match your live workflow configuration.

```bash
python run.py --discover-fields
```

This queries the CFlow API and outputs:
1. A table of every field in your sourcing workflow with its label and API name
2. A ready-to-paste `_build_payload()` code block

**What to do with the output:**

Open `cflow_client.py`, find `_build_payload()`, and confirm the keys on the left side of each line match the **API names** (not display labels) returned by `discover_fields`. Update as needed.

```python
# Example — update these keys to match your CFlow workflow's actual field names:
"form_fields": {
    "Solicitation Title":  tender.get("solicitation_title", ""),
    "Solicitation No":     tender.get("solicitation_no", ""),
    # ... etc
}
```

> 📌 If `discover_fields` can't reach the CFlow API, double-check `CFLOW_BASE_URL`, `CFLOW_API_KEY`, `CFLOW_USER_KEY`, and `CFLOW_USERNAME` in your `.env`.

---

### 4. Validate the Scraper (Dry Run)

Run the scraper against the live CanadaBuys portal without creating any CFlow records:

```bash
python run.py --dry-run --limit 5
```

**Expected output:**
```
Fetching CanadaBuys tender listings...
✅  Found 50 tender(s) on page

Showing first 5 result(s):
──────────────────────────────────────────────────────────────────────
  TENDER 1 of 5
──────────────────────────────────────────────────────────────────────

  CFlow payload that would be sent:

{
    "workflow_name": "Sourcing Workflow",
    "form_fields": {
        "Solicitation Title": "IT Security Assessment Services",
        "Solicitation No": "PW-EZZ-123-00001",
        ...
    }
}
```

**Check:**
- [ ] At least 1 tender returned (portal accessible, filters working)
- [ ] `solicitation_no` is populated and follows a government format (`PW-`, `WS-`, `EN579-`, etc.)
- [ ] `inquiry_link` is a full `https://canadabuys.canada.ca/...` URL
- [ ] `contact_email` contains `@` where populated (some tenders omit contact info)
- [ ] No Python tracebacks

**If you see `Found 0 tenders`:** CSS selectors may need updating. Run with `--visible` to watch the browser in real time:

```bash
python run.py --dry-run --limit 1 --visible
```

Watch what the browser renders, compare to the selectors in `scraper.py`, and update as needed. See [Troubleshooting](#troubleshooting) for common issues.

---

### 5. Test CFlow Integration

Run a live test with draft records (won't trigger the sourcing workflow):

```bash
# In .env, set: CFLOW_SUBMIT_NOW=false
python run.py --limit 3
```

Log into CFlow and verify:
- [ ] 3 draft records appear in the sourcing workflow
- [ ] All 11 fields populated correctly
- [ ] `Source` field reads `"CanadaBuys Auto-Agent"`
- [ ] `Inquiry Link` field is a clickable URL pointing to the correct tender

Once confirmed, **delete the test draft records** from CFlow, then set `CFLOW_SUBMIT_NOW=true` in `.env`.

---

### 6. Test Notifications (Optional)

If using Slack:

```bash
# Add NOTIFY_SLACK_WEBHOOK=https://hooks.slack.com/services/... to .env
python run.py --dry-run --limit 1
```

A summary message should arrive in your configured Slack channel within seconds.

If using email, ensure SMTP credentials are set in `.env` and run the same command.

---

## Project Structure

```
canadabuys-cflow-agent/
├── agent.py                    # Main orchestrator — run this
├── scraper.py                  # Playwright browser scraper
├── cflow_client.py             # CFlow REST API client
├── state.py                    # Deduplication state manager
├── notifier.py                 # Slack + email notifications
├── config.py                   # Environment variable loader
├── run.py                      # CLI entrypoint with flags
├── test_run.py                 # Dry-run validator
├── discover_fields.py          # CFlow field discovery tool
├── .env.example                # Environment variable template
├── .env                        # Your local config (never commit this)
├── .gitignore                  # Includes .env and processed_solicitations.json
├── requirements.txt            # Python dependencies
├── processed_solicitations.json  # Dedup state (auto-created on first run)
├── agent.log                   # Run log (auto-created on first run)
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── canadabuys_listing.html
│   │   └── canadabuys_detail.html
│   ├── unit/
│   │   ├── test_state.py
│   │   ├── test_cflow_client.py
│   │   ├── test_scraper_helpers.py
│   │   ├── test_config.py
│   │   └── test_notifier.py
│   └── integration/
│       ├── test_cflow_api.py
│       ├── test_agent_orchestrator.py
│       └── test_scraper_extraction.py
└── .github/
    └── workflows/
        └── daily_agent.yml     # GitHub Actions cron schedule
```

---

## Common Commands

| Command | Purpose |
|---------|---------|
| `python run.py` | Normal production run |
| `python run.py --dry-run` | Scrape only — no CFlow records created |
| `python run.py --dry-run --limit 5` | Preview first 5 tenders |
| `python run.py --dry-run --visible` | Watch browser navigate portal (debug) |
| `python run.py --discover-fields` | List CFlow form field names |
| `python run.py --reset-state` | Wipe dedup history and reprocess all tenders |
| `python run.py --pages 1` | Only scrape first page of results |
| `pytest tests/` | Run test suite |
| `pytest tests/ -v --tb=short` | Run tests with verbose output |
| `pytest tests/unit/` | Unit tests only (fast, no browser) |

---

## GitHub Actions Deployment

### Step 1 — Push to GitHub

```bash
git init                              # if not already a git repo
git add .
git commit -m "Initial agent setup"
git remote add origin https://github.com/YOUR_ORG/canadabuys-cflow-agent.git
git push -u origin main
```

> Ensure `.gitignore` includes `.env` and `processed_solicitations.json` before pushing.

### Step 2 — Add Repository Secrets

In GitHub: **Settings → Secrets and variables → Actions → New repository secret**

Add each of these as a separate secret:

| Secret Name | Value |
|-------------|-------|
| `CFLOW_BASE_URL` | e.g. `https://us.cflowapps.com` |
| `CFLOW_API_KEY` | Your CFlow API key |
| `CFLOW_USER_KEY` | Your CFlow user key |
| `CFLOW_USERNAME` | Your CFlow login email |
| `CFLOW_WORKFLOW_NAME` | Exact workflow name |
| `NOTIFY_SLACK_WEBHOOK` | *(optional)* Slack webhook URL |

> ⚠️ Do **not** store secrets as repository variables (visible to all collaborators) — use **Secrets** (encrypted, masked in logs).

### Step 3 — Enable and Trigger the Workflow

1. Go to **Actions** tab in your GitHub repository
2. You should see `CanadaBuys → CFlow Daily Agent` in the workflow list
3. Click it → **Run workflow** → **Run workflow** (green button)
4. Watch the run complete — confirm all steps pass and logs look correct

### Step 4 — Verify the Schedule

The workflow is scheduled to run at `0 12 * * 1-5` UTC = **7:00 AM Eastern Time, Monday–Friday**.

To confirm the schedule is active: after the manual run succeeds, check the workflow file is not disabled. GitHub will show the next scheduled run time in the workflow UI.

> 💡 **Adjusting the schedule:** Edit `cron: "0 12 * * 1-5"` in `.github/workflows/daily_agent.yml`. Use https://crontab.guru to preview schedule expressions. Common alternatives:
> - `0 14 * * 1-5` = 9 AM ET (UTC-5)  
> - `0 17 * * 1-5` = 12 PM ET (end-of-day Eastern, records waiting next morning)

### Step 5 — Verify State Persistence Between Runs

After two consecutive runs:

1. Download the `agent.log` artifact from the second run
2. Confirm the log contains `Skipped: N` lines for tenders from the first run
3. Confirm `New: 0` or `New: [only genuinely new tenders since last run]`

If `New` is identical to the first run — the state cache is not persisting. Check that `actions/cache` is not being skipped due to a cache key collision. See [Troubleshooting](#troubleshooting).

---

## CI Pipeline (Tests on Every Push)

The `daily_agent.yml` workflow includes a test step. To also run tests on pull requests, create a separate workflow file:

```yaml
# .github/workflows/ci.yml
name: CI Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          playwright install chromium --with-deps
      - name: Run tests
        run: pytest tests/ -v --tb=short
```

Unit tests run in ~5 seconds. Integration tests with Playwright fixtures run in ~30 seconds. Neither requires live portal or CFlow access.

---

## Troubleshooting

| Problem | Symptoms | Solution |
|---------|----------|---------|
| **`EnvironmentError: Required variable not set`** | Agent fails immediately on startup | Run `cat .env` and confirm all 5 required vars are present and non-empty |
| **`Found 0 tenders`** | Scraper returns empty list | CanadaBuys HTML may have changed. Run `--dry-run --visible` to watch browser. Update CSS selectors in `scraper.py`. |
| **`CFlow returned 401`** | Auth failure on every POST | `CFLOW_API_KEY`, `CFLOW_USER_KEY`, or `CFLOW_USERNAME` is wrong. Re-copy from CFlow dashboard. Check for leading/trailing spaces. |
| **`CFlow returned 422`** | Validation error on POST | Form field names in `_build_payload()` don't match CFlow workflow. Run `--discover-fields` and compare. |
| **`CFlow returned 404`** | Endpoint not found | `CFLOW_BASE_URL` may be wrong. Check your CFlow login URL — it should be `https://us.cflowapps.com` or `https://ap.cflowapps.com` depending on your region. |
| **`playwright._impl._errors.TimeoutError`** | Browser times out waiting for portal | CanadaBuys portal is slow or under maintenance. Increase `timeout_ms` in `ScraperConfig`, or wait and retry. |
| **Duplicate CFlow entries after re-deploy** | Records created twice | `processed_solicitations.json` was not cached between GitHub Actions runs. Check `actions/cache` step in workflow YAML for correct `restore-keys` prefix. |
| **Slack notification not received** | No message in channel | Verify `NOTIFY_SLACK_WEBHOOK` URL is correct. Test it manually: `curl -X POST -H 'Content-type: application/json' --data '{"text":"test"}' YOUR_WEBHOOK_URL` |
| **Email not received** | No email after run | Check spam folder. Verify SMTP credentials. For Gmail: ensure you're using an **App Password** (not your Google account password) — https://myaccount.google.com/apppasswords |
| **`ModuleNotFoundError`** | Import error on any module | Virtual environment not activated. Run `source .venv/bin/activate` (macOS/Linux) or `.venv\Scripts\activate` (Windows). |
| **GitHub Actions run not triggering** | No runs at scheduled time | GitHub sometimes delays cron triggers by up to 15 minutes. Also check that the repository has had a push in the last 60 days — GitHub disables cron on inactive repos. |
| **`actions/cache` miss every run** | State resets each run | The cache key must be consistent. Check that `restore-keys: agent-state-` matches across both the restore and save steps. |

---

## Capturing HTML Fixtures for Tests

Before writing integration tests, capture real portal HTML to use as test fixtures:

```python
# Run this once to capture fixture HTML
import asyncio
from playwright.async_api import async_playwright

async def capture():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        # Listing page
        await page.goto("https://canadabuys.canada.ca/en/tender-opportunities"
                        "?search_filter=&pub%5B1%5D=1&status%5B87%5D=87"
                        "&category%5B153%5D=153&category%5B154%5D=154"
                        "&category%5B156%5D=156&Apply_filters=Apply+filters"
                        "&record_per_page=50&current_tab=t&words=",
                        wait_until="networkidle")
        with open("tests/fixtures/canadabuys_listing.html", "w") as f:
            f.write(await page.content())

        # Detail page — pick any tender link from the listing
        await page.goto("https://canadabuys.canada.ca/en/tender-opportunities/PASTE_A_TENDER_URL",
                        wait_until="networkidle")
        with open("tests/fixtures/canadabuys_detail.html", "w") as f:
            f.write(await page.content())

        await browser.close()
        print("Fixtures captured.")

asyncio.run(capture())
```

Commit the fixture files to the repo. They become the stable ground truth for scraper tests. Re-capture whenever you update CSS selectors after a portal HTML change.

---

## Parallel Run Checklist (Before Retiring Data Miner)

Run the agent alongside the existing manual process for 2 weeks. Each day, compare:

- [ ] Agent CFlow records match the tenders the team manually entered
- [ ] All 11 fields are populated (vs. what Data Miner extracted)
- [ ] No extra tenders in agent output that shouldn't be there
- [ ] No tenders missing from agent output that should be there
- [ ] CFlow `Source` field reads `"CanadaBuys Auto-Agent"` on agent records

After **5 consecutive clean matching days**, the agent is ready to become the sole intake mechanism. Schedule a 30-minute handoff call with the sourcing team to walk through the Slack/email notification format and confirm they know where to look for run summaries.
