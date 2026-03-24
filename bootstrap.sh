#!/bin/bash
# CanadaBuys → CFlow Sourcing Intake Agent — Bootstrap Script
# ─────────────────────────────────────────────────────────────
# Run from inside the folder where you downloaded all project files:
#   cd ~/canadabuys-cflow-agent
#   bash bootstrap.sh
#
# What this does:
#   1. Organises planning docs into docs/
#   2. Creates Python virtual environment
#   3. Installs all dependencies
#   4. Installs Playwright Chromium browser
#   5. Scaffolds tests/ directory and fixture capture script
#   6. Creates .env from .env.example
#   7. Creates .gitignore
#   8. Creates GitHub Actions CI workflow
#   9. Prints next steps

set -e  # Exit immediately on any error

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

PYTHON=${PYTHON:-python3}

echo ""
echo "🚀  Bootstrapping CanadaBuys → CFlow Sourcing Agent..."
echo "    Working in: $PROJECT_DIR"
echo ""

# ─── 0. Verify prerequisites ──────────────────────────────────────────────────

echo "🔍  Checking prerequisites..."

if ! command -v "$PYTHON" &> /dev/null; then
  echo "❌  Python 3 not found. Install from https://python.org (3.12+ required)"
  exit 1
fi

PYVER=$("$PYTHON" -c 'import sys; print(sys.version_info >= (3,12))')
if [ "$PYVER" != "True" ]; then
  echo "❌  Python 3.12+ required. Current: $("$PYTHON" --version)"
  exit 1
fi

echo "    ✓ $("$PYTHON" --version)"

# ─── 1. Organise planning docs ────────────────────────────────────────────────

echo ""
echo "📄  Organising planning docs into docs/..."
mkdir -p docs

for doc in COMPETITIVE_ANALYSIS.md PRD.md USER_FLOWS.md RFC.md \
           ARCHITECTURE.md TESTING.md COST_ESTIMATE.md SETUP.md; do
  [ -f "$doc" ] && mv "$doc" docs/ && echo "    → docs/$doc"
done

# CLAUDE.md and agents/ stay at project root — Claude Code expects them here

# ─── 2. Create virtual environment ───────────────────────────────────────────

echo ""
echo "🐍  Creating Python virtual environment (.venv)..."

if [ ! -d ".venv" ]; then
  "$PYTHON" -m venv .venv
  echo "    ✓ .venv created"
else
  echo "    ✓ .venv already exists — skipping"
fi

# Activate venv for the rest of this script
source .venv/bin/activate

# ─── 3. Create requirements.txt if missing ───────────────────────────────────

if [ ! -f "requirements.txt" ]; then
  echo ""
  echo "📋  Creating requirements.txt..."
cat > requirements.txt << 'REQS'
playwright>=1.43.0
httpx>=0.27.0
python-dotenv>=1.0.0

# Testing
pytest>=8.0
pytest-asyncio>=0.23
pytest-mock>=3.12
respx>=0.21
coverage>=7.0
REQS
  echo "    ✓ requirements.txt created"
fi

# ─── 3b. Create agent source files if missing ────────────────────────────────

echo ""
echo "🔧  Scaffolding agent source files..."

# config.py
if [ ! -f "config.py" ]; then
cat > config.py << 'PYEOF'
"""
Configuration — loads all settings from environment variables.
Fails fast with a clear error if any required variable is missing.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv
from scraper import ScraperConfig
from cflow_client import CFlowConfig

load_dotenv()

def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set.\n"
            f"Add it to your .env file — see docs/SETUP.md."
        )
    return val

def _bool_env(key: str, default: bool = True) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes")

@dataclass
class Config:
    cflow: CFlowConfig
    scraper: ScraperConfig

    @classmethod
    def load(cls) -> "Config":
        cflow = CFlowConfig(
            base_url=_require("CFLOW_BASE_URL"),
            api_key=_require("CFLOW_API_KEY"),
            user_key=_require("CFLOW_USER_KEY"),
            username=_require("CFLOW_USERNAME"),
            workflow_name=_require("CFLOW_WORKFLOW_NAME"),
            submit_immediately=_bool_env("CFLOW_SUBMIT_NOW", default=True),
        )
        default_url = (
            "https://canadabuys.canada.ca/en/tender-opportunities"
            "?search_filter=&pub%5B1%5D=1&status%5B87%5D=87"
            "&category%5B153%5D=153&category%5B154%5D=154&category%5B156%5D=156"
            "&Apply_filters=Apply+filters&record_per_page=50&current_tab=t&words="
        )
        scraper = ScraperConfig(
            search_url=os.getenv("SCRAPER_URL", default_url),
            headless=_bool_env("SCRAPER_HEADLESS", default=True),
        )
        return cls(cflow=cflow, scraper=scraper)
PYEOF
  echo "    ✓ config.py"
fi

# state.py
if [ ! -f "state.py" ]; then
cat > state.py << 'PYEOF'
"""
AgentState — tracks processed solicitation numbers across runs.
Prevents duplicate CFlow entries.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

class AgentState:
    def __init__(self, path: Path = Path("processed_solicitations.json")):
        self._path = path
        self._data: dict[str, Any] = self._load()

    def already_processed(self, solicitation_no: str) -> bool:
        return solicitation_no in self._data

    def mark_processed(self, solicitation_no: str, *, request_id: str = "", title: str = ""):
        self._data[solicitation_no] = {
            "cflow_request_id": request_id,
            "title": title,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }

    def save(self):
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        log.info("State saved: %d solicitations tracked (%s)", len(self._data), self._path)

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                log.info("Loaded state: %d previously processed solicitations", len(data))
                return data
            except Exception as exc:
                log.warning("Could not read state file %s: %s — starting fresh", self._path, exc)
        return {}
PYEOF
  echo "    ✓ state.py"
fi

# scraper.py
if [ ! -f "scraper.py" ]; then
cat > scraper.py << 'PYEOF'
"""
CanadaBuys Portal Scraper — uses Playwright headless Chromium.
Extracts all 11 fields matching the Data Miner recipe v01.
See agents/scraper.md for the CSS selector reference table.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from playwright.async_api import async_playwright, Page, Browser

log = logging.getLogger(__name__)
BASE_URL = "https://canadabuys.canada.ca"

@dataclass
class ScraperConfig:
    search_url: str = (
        "https://canadabuys.canada.ca/en/tender-opportunities"
        "?search_filter=&pub%5B1%5D=1&status%5B87%5D=87"
        "&category%5B153%5D=153&category%5B154%5D=154&category%5B156%5D=156"
        "&Apply_filters=Apply+filters&record_per_page=50&current_tab=t&words="
    )
    headless: bool = True
    timeout_ms: int = 30_000
    slow_mo_ms: int = 0
    max_pages: int = 10

class CanadaBuysScraper:
    def __init__(self, config: ScraperConfig):
        self.config = config
        self._playwright = None
        self._browser: Browser | None = None

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.config.headless,
            slow_mo=self.config.slow_mo_ms,
        )
        return self

    async def __aexit__(self, *_):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def fetch_tender_list(self) -> list[dict[str, Any]]:
        all_tenders: list[dict[str, Any]] = []
        page = await self._browser.new_page()
        try:
            await page.goto(self.config.search_url, timeout=self.config.timeout_ms, wait_until="networkidle")
            await page.wait_for_selector("table.views-table, .view-content", timeout=self.config.timeout_ms)
            for page_num in range(1, self.config.max_pages + 1):
                log.info("Scraping page %d...", page_num)
                tenders = await self._extract_listing(page)
                all_tenders.extend(tenders)
                log.info("  Page %d: %d tender(s) — total: %d", page_num, len(tenders), len(all_tenders))
                next_btn = await page.query_selector(
                    "a[rel='next'], a.pager__item--next, li.pager-next a, .pagination .next a"
                )
                if not next_btn:
                    break
                next_href = await next_btn.get_attribute("href")
                if not next_href:
                    break
                await page.goto(_absolute(next_href), timeout=self.config.timeout_ms, wait_until="networkidle")
                await page.wait_for_selector("table.views-table, .view-content", timeout=self.config.timeout_ms)
        finally:
            await page.close()
        return all_tenders

    async def fetch_tender_detail(self, url: str) -> dict[str, Any]:
        if not url:
            return {}
        page = await self._browser.new_page()
        try:
            await page.goto(url, timeout=self.config.timeout_ms, wait_until="networkidle")
            return await self._extract_detail(page, url)
        except Exception as exc:
            log.warning("Could not load detail page %s: %s", url, exc)
            return {}
        finally:
            await page.close()

    async def _extract_listing(self, page: Page) -> list[dict[str, Any]]:
        tenders = []
        rows = await page.query_selector_all("article.tender-result, tr.odd, tr.even")
        if not rows:
            rows = await page.query_selector_all(".views-row, .search-result")
        for row in rows:
            tender: dict[str, Any] = {}
            title_el = await row.query_selector("h3 a, .title a, td.views-field-title a")
            if title_el:
                tender["solicitation_title"] = _clean(await title_el.inner_text())
                href = await title_el.get_attribute("href")
                tender["inquiry_link"] = _absolute(href)
            else:
                tender["solicitation_title"] = ""
                tender["inquiry_link"] = ""
            sol_el = await row.query_selector(
                "[class*='solicitation-number'], [class*='reference-number'], td.views-field-field-solicitation-number"
            )
            tender["solicitation_no"] = _clean(await sol_el.inner_text()) if sol_el else ""
            client_el = await row.query_selector(
                "[class*='organization'], [class*='client'], td.views-field-field-organization"
            )
            tender["client"] = _clean(await client_el.inner_text()) if client_el else ""
            date_el = await row.query_selector(
                "[class*='closing-date'] time, td.views-field-field-tender-closing-date time, "
                "td.views-field-field-tender-closing-date"
            )
            if date_el:
                dt_attr = await date_el.get_attribute("datetime")
                tender["closing_date"] = dt_attr or _clean(await date_el.inner_text())
            else:
                tender["closing_date"] = ""
            for f in ["gsin_description", "time_and_zone", "notifications", "contact_name", "contact_email", "contact_phone"]:
                tender[f] = ""
            if tender.get("solicitation_no") or tender.get("solicitation_title"):
                tenders.append(tender)
        return tenders

    async def _extract_detail(self, page: Page, url: str) -> dict[str, Any]:
        detail: dict[str, Any] = {"inquiry_link": url}
        gsin_el = await page.query_selector("[class*='gsin'], .field--name-field-gsin .field__item")
        detail["gsin_description"] = _clean(await gsin_el.inner_text()) if gsin_el else ""
        closing_el = await page.query_selector(".field--name-field-tender-closing-date time, [class*='closing-date'] time")
        if closing_el:
            dt = await closing_el.get_attribute("datetime") or _clean(await closing_el.inner_text())
            detail["closing_date"] = dt
        tz_el = await page.query_selector("[class*='timezone'], [class*='time-zone']")
        detail["time_and_zone"] = _clean(await tz_el.inner_text()) if tz_el else ""
        notif_el = await page.query_selector("[class*='amendment'], [class*='notification']")
        detail["notifications"] = _clean(await notif_el.inner_text()) if notif_el else ""
        contact_block = await page.query_selector(".field--name-field-contact, [class*='contact-information']")
        if contact_block:
            name_el = await contact_block.query_selector("[class*='name']")
            detail["contact_name"] = _clean(await name_el.inner_text()) if name_el else ""
            email_el = await contact_block.query_selector("a[href^='mailto:']")
            detail["contact_email"] = (await email_el.inner_text()).strip() if email_el else ""
            phone_el = await contact_block.query_selector("[class*='phone'], [class*='telephone']")
            detail["contact_phone"] = _clean(await phone_el.inner_text()) if phone_el else ""
        else:
            email_el = await page.query_selector("a[href^='mailto:']")
            detail["contact_email"] = (await email_el.inner_text()).strip() if email_el else ""
            phone_el = await page.query_selector("[class*='phone'], [class*='telephone']")
            detail["contact_phone"] = _clean(await phone_el.inner_text()) if phone_el else ""
            detail["contact_name"] = ""
        return detail

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())

def _absolute(href: str | None) -> str:
    if not href:
        return ""
    return href if href.startswith("http") else BASE_URL + href

async def _find_text(parent, selector: str) -> str:
    el = await parent.query_selector(selector)
    return _clean(await el.inner_text()) if el else ""
PYEOF
  echo "    ✓ scraper.py"
fi

# cflow_client.py
if [ ! -f "cflow_client.py" ]; then
cat > cflow_client.py << 'PYEOF'
"""
CFlow REST API Client.
See agents/cflow.md for the field mapping table and API reference.
⚠️  Run: python run.py --discover-fields
    Then update _build_payload() with your actual CFlow field names.
"""
import logging
from dataclasses import dataclass
from typing import Any
import httpx

log = logging.getLogger(__name__)

@dataclass
class CFlowConfig:
    base_url: str
    api_key: str
    user_key: str
    username: str
    workflow_name: str
    submit_immediately: bool = True

class CFlowClient:
    def __init__(self, config: CFlowConfig):
        self.config = config
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "api-key": config.api_key,
                "user-key": config.user_key,
                "username": config.username,
            },
            timeout=30.0,
        )

    async def create_sourcing_request(self, tender: dict[str, Any]) -> str:
        payload = self._build_payload(tender)
        endpoint = "/api/v1/requests" if self.config.submit_immediately else "/api/v1/requests/draft"
        response = await self._http.post(endpoint, json=payload)
        if response.status_code not in (200, 201):
            raise RuntimeError(f"CFlow API returned {response.status_code}: {response.text}")
        data = response.json()
        return str(data.get("request_id") or data.get("id") or data.get("record_id") or data)

    def _build_payload(self, tender: dict[str, Any]) -> dict[str, Any]:
        # ⚠️  Update keys below to match your CFlow workflow field names.
        # Run: python run.py --discover-fields  to get the exact names.
        return {
            "workflow_name": self.config.workflow_name,
            "form_fields": {
                "Solicitation Title":  tender.get("solicitation_title", ""),
                "Solicitation No":     tender.get("solicitation_no", ""),
                "GSIN Description":    tender.get("gsin_description", ""),
                "Inquiry Link":        tender.get("inquiry_link", ""),
                "Closing Date":        tender.get("closing_date", ""),
                "Time and Zone":       tender.get("time_and_zone", ""),
                "Notifications":       tender.get("notifications", ""),
                "Client":              tender.get("client", ""),
                "Contact Name":        tender.get("contact_name", ""),
                "Contact Email":       tender.get("contact_email", ""),
                "Contact Phone":       tender.get("contact_phone", ""),
                "Source":              "CanadaBuys Auto-Agent",
            },
        }

    async def aclose(self):
        await self._http.aclose()
PYEOF
  echo "    ✓ cflow_client.py"
fi

# notifier.py
if [ ! -f "notifier.py" ]; then
cat > notifier.py << 'PYEOF'
"""
Run Summary Notifications — Slack webhook and/or SMTP email.
Configure in .env: NOTIFY_SLACK_WEBHOOK and/or NOTIFY_EMAIL_TO + SMTP_* vars.
"""
import logging
import os
import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
import httpx

log = logging.getLogger(__name__)

@dataclass
class RunSummary:
    run_at: str = ""
    total_found: int = 0
    new_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    new_tenders: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def __post_init__(self):
        if not self.run_at:
            self.run_at = datetime.now().strftime("%Y-%m-%d %H:%M %Z")

class Notifier:
    def __init__(self):
        self.slack_webhook = os.getenv("NOTIFY_SLACK_WEBHOOK", "").strip()
        self.email_to = os.getenv("NOTIFY_EMAIL_TO", "").strip()
        self.email_from = os.getenv("NOTIFY_EMAIL_FROM", "").strip()
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "").strip()
        self.smtp_pass = os.getenv("SMTP_PASS", "").strip()

    async def send(self, summary: RunSummary):
        if self.slack_webhook:
            await self._send_slack(summary)
        if self.email_to and self.smtp_user:
            self._send_email(summary)
        if not self.slack_webhook and not self.email_to:
            log.debug("No notification channels configured — skipping")

    async def _send_slack(self, s: RunSummary):
        emoji = "✅" if s.error_count == 0 else "⚠️"
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} CanadaBuys → CFlow | {s.run_at}"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Found:*\n{s.total_found}"},
                {"type": "mrkdwn", "text": f"*New → CFlow:*\n{s.new_count}"},
                {"type": "mrkdwn", "text": f"*Skipped:*\n{s.skipped_count}"},
                {"type": "mrkdwn", "text": f"*Errors:*\n{s.error_count}"},
            ]},
        ]
        if s.new_tenders:
            lines = "\n".join(
                f"• <{t.get('inquiry_link','')}|{t.get('solicitation_title','(no title)')}> — {t.get('solicitation_no','')} — closes {t.get('closing_date','?')}"
                for t in s.new_tenders[:10]
            )
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*New tenders:*\n{lines}"}})
        if s.errors:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Errors:*\n" + "\n".join(f"• {e}" for e in s.errors[:5])}})
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(self.slack_webhook, json={"blocks": blocks})
                if r.status_code == 200:
                    log.info("Slack notification sent")
                else:
                    log.warning("Slack failed: %d %s", r.status_code, r.text)
        except Exception as exc:
            log.warning("Slack error: %s", exc)

    def _send_email(self, s: RunSummary):
        subject = (f"[CanadaBuys Agent] {s.new_count} new tender(s) → CFlow — {s.run_at}"
                   if s.new_count else f"[CanadaBuys Agent] No new tenders — {s.run_at}")
        rows = "".join(
            f"<tr><td><a href='{t.get('inquiry_link','#')}'>{t.get('solicitation_title','')}</a></td>"
            f"<td>{t.get('solicitation_no','')}</td><td>{t.get('client','')}</td><td>{t.get('closing_date','')}</td></tr>"
            for t in s.new_tenders
        )
        html = f"<html><body><h2>CanadaBuys Agent Run — {s.run_at}</h2><p>Found: {s.total_found} | New: {s.new_count} | Skipped: {s.skipped_count} | Errors: {s.error_count}</p>{'<table border=1 cellpadding=6><tr><th>Title</th><th>Sol No</th><th>Client</th><th>Closing</th></tr>' + rows + '</table>' if rows else '<p>No new tenders.</p>'}</body></html>"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.email_from
        msg["To"] = self.email_to
        msg.attach(MIMEText(html, "html"))
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.sendmail(self.email_from, [self.email_to], msg.as_string())
            log.info("Email sent to %s", self.email_to)
        except Exception as exc:
            log.warning("Email error: %s", exc)
PYEOF
  echo "    ✓ notifier.py"
fi

# agent.py
if [ ! -f "agent.py" ]; then
cat > agent.py << 'PYEOF'
"""
CanadaBuys → CFlow Sourcing Intake Agent
Main orchestrator — see CLAUDE.md for commands and verification steps.
See agents/orchestrator.md for the state lifecycle invariants.
"""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from scraper import CanadaBuysScraper
from cflow_client import CFlowClient
from state import AgentState
from config import Config
from notifier import Notifier, RunSummary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("agent.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

async def run_agent():
    log.info("=" * 60)
    log.info("CanadaBuys → CFlow Agent starting  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    log.info("=" * 60)

    config = Config.load()
    state = AgentState(path=Path("processed_solicitations.json"))
    cflow = CFlowClient(config.cflow)
    notifier = Notifier()
    summary = RunSummary()

    async with CanadaBuysScraper(config.scraper) as scraper:
        log.info("Fetching tender listings from CanadaBuys...")
        tenders = await scraper.fetch_tender_list()
        log.info("Found %d tender(s) total", len(tenders))
        summary.total_found = len(tenders)

        for tender in tenders:
            sol_no = tender.get("solicitation_no", "").strip()
            if not sol_no:
                continue
            if state.already_processed(sol_no):
                summary.skipped_count += 1
                log.debug("Already processed: %s — skipping", sol_no)
                continue
            log.info("New tender: [%s] %s", sol_no, tender.get("solicitation_title", ""))
            try:
                detail = await scraper.fetch_tender_detail(tender.get("inquiry_link", ""))
                tender.update(detail)
                request_id = await cflow.create_sourcing_request(tender)
                log.info("✓ CFlow request created: %s  (%s)", request_id, sol_no)
                state.mark_processed(sol_no, request_id=request_id, title=tender.get("solicitation_title"))
                summary.new_count += 1
                summary.new_tenders.append(tender)
            except Exception as exc:
                log.error("✗ Failed %s: %s", sol_no, exc)
                summary.error_count += 1
                summary.errors.append(f"{sol_no}: {exc}")

    state.save()
    log.info("─" * 60)
    log.info("Done. New: %d | Skipped: %d | Errors: %d | Total: %d",
             summary.new_count, summary.skipped_count, summary.error_count, summary.total_found)
    log.info("=" * 60)
    await notifier.send(summary)

if __name__ == "__main__":
    asyncio.run(run_agent())
PYEOF
  echo "    ✓ agent.py"
fi

# discover_fields.py
if [ ! -f "discover_fields.py" ]; then
cat > discover_fields.py << 'PYEOF'
"""
CFlow Field Discovery Tool — run once before first live submission.
Queries CFlow API and outputs the exact field names for your sourcing workflow,
plus a ready-to-paste _build_payload() code block.

Usage:  python run.py --discover-fields
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
SEP = "─" * 70

async def discover_fields():
    print(f"\n🔍  CFlow Field Discovery\n{SEP}")
    config = Config.load()
    c = config.cflow
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "api-key": c.api_key, "user-key": c.user_key, "username": c.username}

    async with httpx.AsyncClient(base_url=c.base_url, headers=headers, timeout=30) as client:
        workflows_data = None
        for ep in ["/api/v1/workflows", "/api/v1/processes", "/cflownew/api/v1/workflows"]:
            try:
                r = await client.get(ep)
                if r.status_code == 200:
                    workflows_data = r.json()
                    break
            except Exception:
                pass

        if not workflows_data:
            print("\n❌  Could not reach CFlow API. Check credentials in .env.")
            return

        workflows = workflows_data if isinstance(workflows_data, list) else workflows_data.get("data", [])
        print(f"\nWorkflows found:\n")
        for wf in workflows:
            name = wf.get("name") or wf.get("workflow_name") or wf.get("title") or str(wf)
            marker = "  ◀ TARGET" if c.workflow_name.lower() in name.lower() else ""
            print(f"  {name}{marker}")

        target = next((wf for wf in workflows if c.workflow_name.lower() in
                       (wf.get("name") or wf.get("workflow_name") or wf.get("title") or "").lower()), None)
        if not target:
            print(f"\n❌  Workflow '{c.workflow_name}' not found. Update CFLOW_WORKFLOW_NAME in .env.")
            return

        wf_id = target.get("id") or target.get("workflow_id")
        wf_name = target.get("name") or target.get("workflow_name") or target.get("title")
        print(f"\n✅  Target workflow: '{wf_name}' (id={wf_id})\n")

        fields_data = None
        for ep in [f"/api/v1/workflows/{wf_id}/fields", f"/api/v1/processes/{wf_id}/fields"]:
            try:
                r = await client.get(ep)
                if r.status_code == 200:
                    fields_data = r.json()
                    break
            except Exception:
                pass

        if not fields_data:
            print("⚠️  Could not retrieve fields. Check field names manually in the CFlow form builder.")
            return

        fields = fields_data if isinstance(fields_data, list) else fields_data.get("fields", fields_data.get("data", []))
        print(f"{'#':<4} {'Label':<40} {'API Name':<35} {'Type'}")
        print(f"{'─'*4} {'─'*40} {'─'*35} {'─'*15}")
        for i, f in enumerate(fields, 1):
            label = f.get("label") or f.get("field_label") or f.get("name") or ""
            api_name = f.get("api_name") or f.get("key") or f.get("field_name") or f.get("id") or ""
            ftype = f.get("type") or f.get("field_type") or ""
            print(f"{i:<4} {label:<40} {api_name:<35} {ftype}")

        print(f"\n{SEP}")
        print("Copy this into cflow_client.py → _build_payload() → form_fields:\n")
        recipe = ["Solicitation Title","Solicitation No","GSIN Description","Inquiry Link",
                  "Closing Date","Time and Zone","Notifications","Client","Contact Name","Contact Email","Contact Phone"]
        keys = ["solicitation_title","solicitation_no","gsin_description","inquiry_link",
                "closing_date","time_and_zone","notifications","client","contact_name","contact_email","contact_phone"]
        print('            "form_fields": {')
        for recipe_name, scraper_key in zip(recipe, keys):
            match = next((f for f in fields if recipe_name.lower() in
                          (f.get("label") or f.get("field_label") or f.get("name") or "").lower()), None)
            cflow_key = (match.get("api_name") or match.get("key") or match.get("field_name") or f'"{recipe_name}"') if match else f'"{recipe_name}"  # ← VERIFY'
            print(f'                "{cflow_key}": tender.get("{scraper_key}", ""),')
        print('                "Source": "CanadaBuys Auto-Agent",')
        print('            },')

if __name__ == "__main__":
    asyncio.run(discover_fields())
PYEOF
  echo "    ✓ discover_fields.py"
fi

# test_run.py
if [ ! -f "test_run.py" ]; then
cat > test_run.py << 'PYEOF'
"""
Dry-run validator — scrapes CanadaBuys and prints CFlow payloads without creating records.
Usage: python run.py --dry-run --limit 5
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scraper import CanadaBuysScraper, ScraperConfig
from config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
SEP = "─" * 70

async def dry_run(limit: int, fetch_detail: bool, headless: bool):
    log.info("DRY RUN — no CFlow records will be created")
    try:
        config = Config.load()
        scraper_config = config.scraper
        scraper_config.headless = headless
    except EnvironmentError:
        scraper_config = ScraperConfig(headless=headless)
        config = None

    print(f"\n🌐  Fetching CanadaBuys tender listings...\n")
    async with CanadaBuysScraper(scraper_config) as scraper:
        tenders = await scraper.fetch_tender_list()

    print(f"\n✅  Found {len(tenders)} tender(s)\n")
    sample = tenders[:limit]

    for i, tender in enumerate(sample, 1):
        print(f"{SEP}\n  TENDER {i} of {len(sample)}\n{SEP}")
        if fetch_detail and tender.get("inquiry_link"):
            async with CanadaBuysScraper(scraper_config) as scraper:
                detail = await scraper.fetch_tender_detail(tender["inquiry_link"])
            tender.update(detail)
        if config:
            from cflow_client import CFlowClient
            payload = CFlowClient(config.cflow)._build_payload(tender)
            print(f"\n  CFlow payload:\n\n{json.dumps(payload, indent=4)}\n")
        else:
            print(f"\n  Raw fields:\n\n{json.dumps(tender, indent=4)}\n")

    print(f"{SEP}\n✅  Dry run complete. {len(sample)} tender(s) previewed.\n")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--detail", action="store_true")
    p.add_argument("--no-head", action="store_true")
    args = p.parse_args()
    asyncio.run(dry_run(limit=args.limit, fetch_detail=args.detail, headless=not args.no_head))
PYEOF
  echo "    ✓ test_run.py"
fi

# run.py
if [ ! -f "run.py" ]; then
cat > run.py << 'PYEOF'
"""
CLI entrypoint. See CLAUDE.md for all available flags.
"""
import argparse, asyncio, logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def parse_args():
    p = argparse.ArgumentParser(description="CanadaBuys → CFlow Agent")
    p.add_argument("--dry-run", action="store_true", help="Scrape only — no CFlow records")
    p.add_argument("--limit", type=int, default=None, help="Max tenders to process")
    p.add_argument("--reset-state", action="store_true", help="Wipe dedup history")
    p.add_argument("--discover-fields", action="store_true", help="List CFlow form fields")
    p.add_argument("--visible", action="store_true", help="Show browser window (debug)")
    p.add_argument("--pages", type=int, default=None, help="Max pages to scrape")
    p.add_argument("--no-detail", action="store_true", help="Skip detail pages")
    return p.parse_args()

async def main():
    args = parse_args()

    if args.discover_fields:
        import discover_fields
        await discover_fields.discover_fields()
        return

    from config import Config
    config = Config.load()

    if args.visible:
        config.scraper.headless = False
    if args.pages:
        config.scraper.max_pages = args.pages

    state_path = Path("processed_solicitations.json")
    if args.reset_state and state_path.exists():
        state_path.unlink()
        print("State reset — all solicitations will be reprocessed")

    if args.dry_run:
        import test_run
        await test_run.dry_run(
            limit=args.limit or 5,
            fetch_detail=not args.no_detail,
            headless=not args.visible,
        )
        return

    import agent
    await agent.run_agent()

if __name__ == "__main__":
    asyncio.run(main())
PYEOF
  echo "    ✓ run.py"
fi

# ─── 4. Install Python dependencies ──────────────────────────────────────────

echo ""
echo "📦  Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "    ✓ Dependencies installed"

# ─── 4. Install Playwright Chromium ──────────────────────────────────────────

echo ""
echo "🌐  Installing Playwright Chromium browser..."
playwright install chromium
echo "    ✓ Chromium installed"

# ─── 5. Scaffold tests/ directory structure ───────────────────────────────────

echo ""
echo "🧪  Scaffolding test directories..."
mkdir -p tests/unit
mkdir -p tests/integration
mkdir -p tests/fixtures

# Create empty conftest.py if it doesn't exist
if [ ! -f "tests/conftest.py" ]; then
cat > tests/conftest.py << 'CONFTEST'
"""
Shared pytest fixtures for the CanadaBuys → CFlow agent tests.
"""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from config import CFlowConfig, ScraperConfig
from cflow_client import CFlowClient
from state import AgentState


@pytest.fixture
def tmp_state(tmp_path):
    """AgentState backed by a temp file — isolated per test."""
    return AgentState(path=tmp_path / "state.json")


@pytest.fixture
def cflow_config():
    return CFlowConfig(
        base_url="https://us.cflowapps.com",
        api_key="test-api-key",
        user_key="test-user-key",
        username="test@example.com",
        workflow_name="Sourcing Workflow",
    )


@pytest.fixture
def scraper_config():
    return ScraperConfig(headless=True, max_pages=1)


@pytest.fixture
def sample_tender():
    return {
        "solicitation_title": "IT Security Assessment Services",
        "solicitation_no": "PW-EZZ-123-00001",
        "gsin_description": "EDP - Professional Services",
        "inquiry_link": "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/PW-EZZ-123-00001",
        "closing_date": "2026-04-15",
        "time_and_zone": "14:00 Eastern",
        "notifications": "0 amendments",
        "client": "Shared Services Canada",
        "contact_name": "Jane Smith",
        "contact_email": "jane.smith@ssc-spc.gc.ca",
        "contact_phone": "613-555-0100",
    }
CONFTEST
  echo "    ✓ tests/conftest.py created"
fi

# Create placeholder fixture HTML files (populated during setup — see SETUP.md)
if [ ! -f "tests/fixtures/canadabuys_listing.html" ]; then
  echo "<!-- Capture this file using the command in SETUP.md → 'Capturing HTML Fixtures' -->" \
    > tests/fixtures/canadabuys_listing.html
  echo "    ✓ tests/fixtures/canadabuys_listing.html (placeholder — see SETUP.md)"
fi

if [ ! -f "tests/fixtures/canadabuys_detail.html" ]; then
  echo "<!-- Capture this file using the command in SETUP.md → 'Capturing HTML Fixtures' -->" \
    > tests/fixtures/canadabuys_detail.html
  echo "    ✓ tests/fixtures/canadabuys_detail.html (placeholder — see SETUP.md)"
fi

# Create fixture capture helper script
if [ ! -f "capture_fixtures.py" ]; then
cat > capture_fixtures.py << 'CAPTURE'
"""
One-time script to capture real CanadaBuys HTML into test fixtures.
Run this once after initial setup, and again whenever scraper selectors break.

Usage:
  source .venv/bin/activate
  python capture_fixtures.py
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

SEARCH_URL = (
    "https://canadabuys.canada.ca/en/tender-opportunities"
    "?search_filter=&pub%5B1%5D=1&status%5B87%5D=87"
    "&category%5B153%5D=153&category%5B154%5D=154&category%5B156%5D=156"
    "&Apply_filters=Apply+filters&record_per_page=50&current_tab=t&words="
)

async def capture():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        print("Navigating to CanadaBuys listing page...")
        await page.goto(SEARCH_URL, wait_until="networkidle")
        Path("tests/fixtures/canadabuys_listing.html").write_text(await page.content())
        print("✓ Listing page captured → tests/fixtures/canadabuys_listing.html")

        # Get first tender link from the listing
        link = await page.query_selector("h3 a, .title a, td.views-field-title a")
        if link:
            href = await link.get_attribute("href")
            detail_url = href if href.startswith("http") else f"https://canadabuys.canada.ca{href}"
            print(f"Navigating to detail page: {detail_url}")
            await page.goto(detail_url, wait_until="networkidle")
            Path("tests/fixtures/canadabuys_detail.html").write_text(await page.content())
            print("✓ Detail page captured → tests/fixtures/canadabuys_detail.html")
        else:
            print("⚠️  Could not find a tender link on the listing page.")
            print("   Navigate manually in the browser window and press Enter here.")
            input()

        await browser.close()
        print("\n✅ Fixtures captured. Commit tests/fixtures/ to the repository.")

asyncio.run(capture())
CAPTURE
  echo "    ✓ capture_fixtures.py created (run this to populate test fixtures)"
fi

# ─── 6. Create .env from .env.example ────────────────────────────────────────

echo ""
echo "⚙️   Setting up environment configuration..."

if [ ! -f ".env.example" ]; then
cat > .env.example << 'ENVEOF'
# ── CFlow Credentials ─────────────────────────────────────────────────────────
# API key:  CFlow → Admin → Security Settings → API Settings
# User key: CFlow → (avatar) → Profile → API Key
# Workflow name must match exactly (case-sensitive)

CFLOW_BASE_URL=https://us.cflowapps.com
CFLOW_API_KEY=your_api_key_here
CFLOW_USER_KEY=your_user_key_here
CFLOW_USERNAME=your@email.com
CFLOW_WORKFLOW_NAME=Sourcing Workflow

# Set to "false" to save as draft instead of submitting immediately
CFLOW_SUBMIT_NOW=true

# ── Scraper Settings ──────────────────────────────────────────────────────────
# Set to "false" to watch the browser while debugging
SCRAPER_HEADLESS=true

# Override the CanadaBuys search URL if your filter criteria change
# SCRAPER_URL=https://canadabuys.canada.ca/en/tender-opportunities?...

# ── Notifications (optional) ──────────────────────────────────────────────────
# NOTIFY_SLACK_WEBHOOK=https://hooks.slack.com/services/xxx/yyy/zzz

# NOTIFY_EMAIL_TO=recipient@yourcompany.com
# NOTIFY_EMAIL_FROM=sender@yourcompany.com
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=587
# SMTP_USER=sender@yourcompany.com
# SMTP_PASS=your_app_password_here
ENVEOF
  echo "    ✓ .env.example created"
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "    ✓ .env created from .env.example"
  echo "    ⚠️  Edit .env and add your CFlow credentials before running the agent"
else
  echo "    ✓ .env already exists — skipping"
fi

# ─── 7. Create .gitignore ────────────────────────────────────────────────────

echo ""
echo "📝  Creating .gitignore..."

if [ ! -f ".gitignore" ]; then
cat > .gitignore << 'GITIGNORE'
# Environment — NEVER commit credentials
.env

# Runtime state — contains processed solicitation numbers
# Exclude from version control; persisted via GitHub Actions cache
processed_solicitations.json

# Python
.venv/
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
*.egg-info/
dist/
build/
.pytest_cache/
.coverage
htmlcov/

# Playwright browser binaries
# (installed fresh in CI via playwright install chromium)
# Local path varies by OS — uncomment if needed:
# ~/Library/Caches/ms-playwright/
# ~/.cache/ms-playwright/

# Logs
agent.log
*.log

# IDE
.vscode/
.idea/
*.swp
*.swo
.DS_Store
GITIGNORE
  echo "    ✓ .gitignore created"
else
  echo "    ✓ .gitignore already exists — skipping"
fi

# ─── 8. GitHub Actions CI workflow ───────────────────────────────────────────

echo ""
echo "⚡  Creating GitHub Actions CI workflow..."
mkdir -p .github/workflows

if [ ! -f ".github/workflows/ci.yml" ]; then
cat > .github/workflows/ci.yml << 'CI'
name: CI Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Cache pip
        uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: ${{ runner.os }}-pip-${{ hashFiles('requirements.txt') }}

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          playwright install chromium --with-deps

      - name: Run unit tests (fast — no browser)
        run: pytest tests/unit/ -v --tb=short

      - name: Run integration tests (mocked browser + API)
        run: pytest tests/integration/ -v --tb=short
CI
  echo "    ✓ .github/workflows/ci.yml created"
else
  echo "    ✓ CI workflow already exists — skipping"
fi

# ─── 9. Verify installation ───────────────────────────────────────────────────

echo ""
echo "🔬  Verifying installation..."

"$PYTHON" -c "from playwright.async_api import async_playwright; print('    ✓ playwright (ok)')"
"$PYTHON" -c "import httpx; print('    ✓ httpx', httpx.__version__)"
"$PYTHON" -c "import dotenv; print('    ✓ python-dotenv (ok)')"

# ─── Print next steps ─────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅  CanadaBuys → CFlow Agent bootstrapped successfully!"
echo ""
echo "  📁  docs/              ← PRD, RFC, Architecture, etc."
echo "  📄  CLAUDE.md          ← Claude Code reads this first"
echo "  📁  agents/            ← scraper.md, cflow.md, orchestrator.md"
echo "  📄  .env               ← add your CFlow credentials here"
echo "  📄  capture_fixtures.py ← run once to populate test fixtures"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Next steps:"
echo ""
echo "  1. Add CFlow credentials to .env"
echo "     (see docs/SETUP.md → Required Variables)"
echo ""
echo "  2. Discover your CFlow form field names:"
echo "     source .venv/bin/activate"
echo "     python run.py --discover-fields"
echo ""
echo "  3. Update the field mapping in cflow_client.py → _build_payload()"
echo "     (paste the generated code from step 2)"
echo ""
echo "  4. Capture HTML fixtures for tests (one-time):"
echo "     python capture_fixtures.py"
echo ""
echo "  5. Validate the scraper:"
echo "     python run.py --dry-run --limit 5"
echo ""
echo "  6. When ready to build tests, open Claude Code in this directory and say:"
echo "     'Read CLAUDE.md and docs/. Implement the test suite from docs/TESTING.md'"
echo ""
echo "  7. Deploy to GitHub Actions (see docs/SETUP.md → GitHub Actions Deployment)"
echo ""
