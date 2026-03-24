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

from scraper import CanadaBuysScraper, WEEKLY_URL
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

    # Saturday → weekly filters (Open + Goods + Last 7 days)
    if datetime.now().weekday() == 5:  # 5 = Saturday
        log.info("Saturday detected — using weekly filters (Goods, Last 7 days)")
        config.scraper.search_url = WEEKLY_URL

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
            link = tender.get("inquiry_link", "").strip()
            if not link:
                continue

            # Fetch detail first — solicitation_no comes from the detail page.
            try:
                detail = await scraper.fetch_tender_detail(link)
                tender.update(detail)
            except Exception as exc:
                log.error("✗ Failed to fetch detail for %s: %s", link, exc)
                summary.error_count += 1
                summary.errors.append(f"{link}: {exc}")
                continue

            sol_no = tender.get("solicitation_no", "").strip()
            dedup_key = sol_no or link
            if state.already_processed(dedup_key):
                summary.skipped_count += 1
                log.debug("Already processed: %s — skipping", dedup_key)
                continue

            log.info("New tender: [%s] %s", sol_no, tender.get("solicitation_title", ""))
            try:
                request_id = await cflow.create_sourcing_request(tender)
                log.info("✓ CFlow request created: %s  (%s)", request_id, sol_no)
                state.mark_processed(dedup_key, request_id=request_id, title=tender.get("solicitation_title"))
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
