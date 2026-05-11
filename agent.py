"""
CanadaBuys → CFlow Sourcing Intake Agent
Main orchestrator — see CLAUDE.md for commands and verification steps.
See agents/orchestrator.md for the state lifecycle invariants.

Phase 2: If DATABASE_URL is set, tenders are staged to PostgreSQL for
dashboard review. Otherwise, falls back to direct CFlow submission (legacy).
"""
import asyncio
import logging
import os
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

import zipfile


def _extract_english_pdf_from_zip(zip_path: str, extract_dir: str) -> list[str]:
    """Extract English PDFs from a ZIP file. Returns list of extracted file paths."""
    extracted = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                lower = name.lower()
                # Skip French files
                if any(fr in lower for fr in ["_fr.", "-fr.", "_fre.", "demande", "français", "francais"]):
                    continue
                # Extract PDFs and spreadsheets
                if lower.endswith((".pdf", ".xlsx", ".xls", ".doc", ".docx")):
                    dest = os.path.join(extract_dir, os.path.basename(name))
                    with open(dest, "wb") as f:
                        f.write(zf.read(name))
                    extracted.append(dest)
                    log.info("  Extracted from ZIP: %s", os.path.basename(name))
    except Exception as exc:
        log.warning("  ZIP extraction failed for %s: %s", zip_path, exc)
    return extracted


def _resolve_downloaded_files(downloaded_files: list[str], download_dir: str) -> list[str]:
    """If any downloaded files are ZIPs, extract them and return the actual files."""
    resolved = []
    for fpath in downloaded_files:
        if fpath.lower().endswith(".zip"):
            extracted = _extract_english_pdf_from_zip(fpath, download_dir)
            resolved.extend(extracted)
        else:
            resolved.append(fpath)
    return resolved


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("agent.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


def _use_db() -> bool:
    """Check if Phase 2 PostgreSQL mode is enabled."""
    return bool(os.environ.get("DATABASE_URL", ""))


async def run_agent():
    log.info("=" * 60)
    log.info("CanadaBuys → CFlow Agent starting  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    log.info("=" * 60)

    start_time = time.monotonic()
    use_db = _use_db()

    if use_db:
        import db
        # Phase 2: only need scraper config, not CFlow config
        from dotenv import load_dotenv
        load_dotenv()
        from scraper import ScraperConfig
        scraper_config = ScraperConfig(
            headless=os.environ.get("SCRAPER_HEADLESS", "true").strip().lower() in ("1", "true", "yes"),
        )
        await db.init_schema()
        log.info("Phase 2 mode: staging tenders to PostgreSQL for dashboard review")
    else:
        log.info("Legacy mode: direct CFlow submission")

    config = Config.load() if not use_db else None

    # Saturday → weekly filters (Open + Goods + Last 7 days)
    if datetime.now().weekday() == 5:  # 5 = Saturday
        log.info("Saturday detected — using weekly filters (Goods, Last 7 days)")
        if use_db:
            scraper_config.search_url = WEEKLY_URL
        else:
            config.scraper.search_url = WEEKLY_URL

    state = AgentState(path=Path("processed_solicitations.json"))
    cflow = CFlowClient(config.cflow) if config else None
    notifier = Notifier()
    summary = RunSummary()
    effective_scraper_config = scraper_config if use_db else config.scraper

    download_dir = tempfile.mkdtemp(prefix="sourcing_agent_")
    try:
      async with CanadaBuysScraper(effective_scraper_config) as scraper:
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

            # Dedup: check DB if Phase 2, otherwise JSON state
            if use_db:
                import db as _db
                if sol_no and await _db.tender_exists(sol_no):
                    summary.skipped_count += 1
                    log.debug("Already in DB: %s — skipping", sol_no)
                    continue
                if await _db.tender_exists_by_link(link):
                    summary.skipped_count += 1
                    log.debug("Already in DB (by link): %s — skipping", link)
                    continue
            else:
                if state.already_processed(dedup_key):
                    summary.skipped_count += 1
                    log.debug("Already processed: %s — skipping", dedup_key)
                    continue

            bid_platform = tender.get("bid_platform", "CanadaBuys")
            log.info("New tender: [%s] %s (platform: %s)", sol_no, tender.get("solicitation_title", ""), bid_platform)

            # Download solicitation files — always try CanadaBuys first,
            # even for SAP tenders (some have files on both platforms).
            downloaded_files: list[str] = []
            try:
                downloaded_files = await scraper.download_solicitation(link, download_dir)
                if downloaded_files:
                    summary.files_downloaded += len(downloaded_files)
                    log.info("  Downloaded %d file(s) from CanadaBuys for %s", len(downloaded_files), sol_no)
            except Exception as exc:
                log.debug("  CanadaBuys download attempt for %s: %s", sol_no, exc)

            # If SAP and no files from CanadaBuys, try SAP auto-download
            if bid_platform == "SAP":
                summary.sap_flagged += 1
                if not downloaded_files:
                    sap_user = os.environ.get("SAP_USERNAME", "")
                    if sap_user:
                        try:
                            from sap_client import SAPClient
                            # Use a FRESH browser context for SAP — CanadaBuys cookies
                            # and cache-busting headers interfere with SAP's SPA rendering
                            sap_context = await scraper._browser.new_context(
                                user_agent=scraper._USER_AGENT,
                                accept_downloads=True,
                                viewport={"width": 1280, "height": 900},
                            )
                            try:
                                sap = SAPClient(sap_context)
                                sap_link = tender.get("sap_link", "") or tender.get("inquiry_link", "")
                                downloaded_files = await sap.download_solicitation(sap_link, download_dir)
                                if downloaded_files:
                                    summary.files_downloaded += len(downloaded_files)
                                    log.info("  SAP download: %d file(s) for %s", len(downloaded_files), sol_no)
                                else:
                                    log.info("  No files from CanadaBuys or SAP for %s", sol_no)
                            finally:
                                await sap_context.close()
                        except Exception as exc:
                            log.warning("  SAP download failed for %s: %s", sol_no, exc)
                    else:
                        log.info("  SAP tender, no CanadaBuys files, no SAP credentials — manual download needed")

            # Resolve ZIPs to actual files (SAP downloads are typically ZIPs)
            if downloaded_files:
                downloaded_files = _resolve_downloaded_files(downloaded_files, download_dir)

            if use_db:
                # Phase 2: stage to PostgreSQL for dashboard review
                if not sol_no:
                    log.warning("  Empty solicitation_no — skipping DB staging for %s", link)
                    summary.error_count += 1
                    summary.errors.append(f"Empty sol_no: {link}")
                    continue
                try:
                    import db as _db

                    notes: list[str] = []

                    # Collect processing notes
                    if bid_platform == "SAP":
                        if downloaded_files:
                            notes.append(f"SAP tender — {len(downloaded_files)} file(s) downloaded")
                        elif not os.environ.get("SAP_USERNAME"):
                            notes.append("SAP tender — no SAP credentials configured, manual download needed")
                        else:
                            notes.append("SAP tender — login failed, manual download needed")
                    elif not downloaded_files:
                        notes.append("No solicitation documents found on CanadaBuys")

                    tender_id = await _db.stage_tender(tender, assigned_associate="")
                    if tender_id:
                        log.info("✓ Staged to DB: id=%d  (%s)", tender_id, sol_no)

                        # Write initial notes
                        if notes:
                            await _db.add_processing_note(tender_id, "\n".join(notes))

                        # LLM extraction if solicitation was downloaded
                        if downloaded_files:
                            # Pick the best English PDF for extraction
                            pdf_files = [f for f in downloaded_files if f.lower().endswith(".pdf")]
                            en_pdfs = [f for f in pdf_files if not any(
                                fr in os.path.basename(f).lower()
                                for fr in ["_fr.", "-fr.", "_fre.", "demande", "français"]
                            )]
                            extract_file = (en_pdfs or pdf_files or downloaded_files)[0]
                            log.info("  Extracting from: %s", os.path.basename(extract_file))
                            await _db.add_processing_note(tender_id, f"Downloaded {len(downloaded_files)} file(s) — extracting from: {os.path.basename(extract_file)}")

                            await _db.update_tender_extraction(
                                tender_id, solicitation_path=extract_file
                            )
                            try:
                                from extractor import extract_from_pdf
                                from classifier import classify_and_save_csv
                                extraction = await extract_from_pdf(extract_file)
                                if extraction:
                                    classified = classify_and_save_csv(
                                        extraction, download_dir, sol_no=sol_no
                                    )
                                    # For display: use text for Regular, formatted string for Multiple
                                    req_display = classified.get("requirements_text", "")
                                    if not req_display and extraction.get("requirements"):
                                        reqs = extraction["requirements"]
                                        if isinstance(reqs, list):
                                            req_display = "\n".join(
                                                f"Item {r.get('item','')}: {r.get('description','')} — Qty: {r.get('quantity','')} {r.get('unit_of_issue','')}"
                                                for r in reqs
                                            )
                                        else:
                                            req_display = str(reqs)

                                    await _db.update_tender_extraction(
                                        tender_id,
                                        summary=extraction.get("summary_of_contract", ""),
                                        requirements=req_display,
                                        mandatory_criteria=extraction.get("mandatory_criteria", ""),
                                        submission_method=extraction.get("submission_method", ""),
                                        file_type=classified.get("file_type", ""),
                                        requirements_csv_path=classified.get("csv_path", ""),
                                        requirements_csv=classified.get("requirements_csv", ""),
                                    )
                                    log.info("  LLM extraction complete: file_type=%s", classified.get("file_type", ""))
                                    await _db.add_processing_note(tender_id, f"LLM extraction complete — file type: {classified.get('file_type', 'unknown')}")
                                else:
                                    await _db.add_processing_note(tender_id, "LLM extraction returned no results")
                            except Exception as exc:
                                log.warning("  LLM extraction failed for %s: %s", sol_no, exc)
                                await _db.add_processing_note(tender_id, f"LLM extraction failed: {exc}")
                        summary.new_count += 1
                        summary.new_tenders.append(tender)
                    else:
                        summary.skipped_count += 1
                except Exception as exc:
                    log.error("✗ Failed to stage %s: %s", sol_no, exc)
                    summary.error_count += 1
                    summary.errors.append(f"{sol_no}: {exc}")
            else:
                # Legacy: direct CFlow submission
                try:
                    request_id = await cflow.create_sourcing_request(tender)
                    log.info("✓ CFlow request created: %s  (%s)", request_id, sol_no)

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

    if not use_db:
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

    if use_db:
        import db
        await db.close_pool()

if __name__ == "__main__":
    asyncio.run(run_agent())
