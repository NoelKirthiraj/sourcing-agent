"""
CanadaBuys → CFlow Sourcing Intake Agent
Main orchestrator — see CLAUDE.md for commands and verification steps.
See agents/orchestrator.md for the state lifecycle invariants.
"""
import asyncio
import logging
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from scraper import CanadaBuysScraper, WEEKLY_URL
from cflow_client import CFlowClient
from state import AgentState
from config import Config
from notifier import Notifier, RunSummary
import dashboard_data

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

    start_time = time.monotonic()
    config = Config.load()

    # Saturday → weekly filters (Open + Goods + Last 7 days)
    if datetime.now().weekday() == 5:  # 5 = Saturday
        log.info("Saturday detected — using weekly filters (Goods, Last 7 days)")
        config.scraper.search_url = WEEKLY_URL

    state = AgentState(path=Path("processed_solicitations.json"))
    cflow = CFlowClient(config.cflow)
    notifier = Notifier()
    summary = RunSummary()

    download_dir = tempfile.mkdtemp(prefix="sourcing_agent_")
    try:
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

            bid_platform = tender.get("bid_platform", "CanadaBuys")
            log.info("New tender: [%s] %s (platform: %s)", sol_no, tender.get("solicitation_title", ""), bid_platform)

            # Download solicitation files from CanadaBuys if available.
            downloaded_files: list[str] = []
            if bid_platform != "SAP":
                try:
                    downloaded_files = await scraper.download_solicitation(link, download_dir)
                    if downloaded_files:
                        summary.files_downloaded += len(downloaded_files)
                        log.info("  Downloaded %d file(s) for %s", len(downloaded_files), sol_no)
                except Exception as exc:
                    log.warning("  File download failed for %s: %s", sol_no, exc)
            else:
                summary.sap_flagged += 1
                log.info("  SAP tender — flagged for manual solicitation download")

            try:
                request_id = await cflow.create_sourcing_request(tender)
                log.info("✓ CFlow request created: %s  (%s)", request_id, sol_no)

                # Upload downloaded solicitation files to the CFlow record.
                for fpath in downloaded_files:
                    try:
                        uploaded = await cflow.attach_solicitation(request_id, fpath)
                        if uploaded:
                            summary.files_uploaded += 1
                    except Exception as exc:
                        log.warning("  File upload failed for %s: %s", fpath, exc)

                state.mark_processed(dedup_key, request_id=request_id, title=tender.get("solicitation_title"), link=link)
                summary.new_count += 1
                summary.new_tenders.append(tender)
            except Exception as exc:
                log.error("✗ Failed %s: %s", sol_no, exc)
                summary.error_count += 1
                summary.errors.append(f"{sol_no}: {exc}")
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)

    state.save()
    log.info("─" * 60)
    log.info("Done. New: %d | Skipped: %d | Errors: %d | Total: %d | Files: %d↓ %d↑ | SAP: %d",
             summary.new_count, summary.skipped_count, summary.error_count, summary.total_found,
             summary.files_downloaded, summary.files_uploaded, summary.sap_flagged)
    log.info("=" * 60)
    await notifier.send(summary)

    summary.duration_seconds = time.monotonic() - start_time
    summary.mode = "weekly" if datetime.now().weekday() == 5 else "daily"
    dashboard_data.record_run(summary, data_dir=Path("data"))

if __name__ == "__main__":
    asyncio.run(run_agent())
