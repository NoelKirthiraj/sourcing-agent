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
    p.add_argument("--weekly", action="store_true", help="Use weekly filters (Open + Goods + Last 7 days)")
    p.add_argument("--scrape-only", action="store_true", help="Scrape + record dashboard data, skip CFlow")
    return p.parse_args()

async def main():
    args = parse_args()

    state_path = Path("processed_solicitations.json")

    if args.discover_fields:
        import discover_fields
        await discover_fields.discover_fields()
        return

    if args.reset_state and not args.scrape_only and state_path.exists():
        state_path.unlink()
        print("State reset — all solicitations will be reprocessed")

    if args.scrape_only:
        from scraper import CanadaBuysScraper, ScraperConfig, WEEKLY_URL
        from state import AgentState
        from notifier import RunSummary
        import dashboard_data, time, os, signal

        # Separate state file — never poisons the CFlow production dedup state
        scrape_state_path = Path("processed_dashboard.json")
        if args.reset_state and scrape_state_path.exists():
            scrape_state_path.unlink()

        scraper_config = ScraperConfig(
            search_url=WEEKLY_URL if args.weekly else ScraperConfig.search_url,
            headless=not args.visible if hasattr(args, 'visible') else True,
        )
        mode = "weekly" if args.weekly else "daily"
        start = time.monotonic()
        state = AgentState(path=scrape_state_path)
        summary = RunSummary(mode=mode)
        interrupted = False

        def handle_signal(signum, frame):
            nonlocal interrupted
            interrupted = True
            print(f"\nSignal {signum} received — saving partial results...")

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        try:
            async with CanadaBuysScraper(scraper_config) as scraper:
                tenders = await scraper.fetch_tender_list()
                print(f"\nFound {len(tenders)} tender(s) on portal")
                summary.total_found = len(tenders)
                for tender in tenders:
                    if interrupted:
                        print("\nInterrupted — saving partial results...")
                        break

                    link = tender.get("inquiry_link", "").strip()
                    if not link:
                        continue

                    # Fast dedup: check by link before fetching detail page
                    if state.already_processed_by_link(link):
                        summary.skipped_count += 1
                        title = tender.get("solicitation_title", link[:40])
                        print(f"  [skip] {title[:60]}")
                        continue

                    # New tender — fetch full details
                    try:
                        detail = await scraper.fetch_tender_detail(link)
                        tender.update(detail)
                    except Exception as exc:
                        summary.error_count += 1
                        summary.errors.append(f"{link}: {exc}")
                        continue

                    # Reject empty detail — detail page failed silently
                    sol_no = tender.get("solicitation_no", "").strip()
                    if not sol_no:
                        summary.error_count += 1
                        summary.errors.append(f"{link}: detail extraction returned no solicitation_no")
                        print(f"  [fail] {link[:60]} — no solicitation_no extracted")
                        continue

                    dedup_key = sol_no

                    # Double-check by sol_no (in case link changed but same tender)
                    if state.already_processed(dedup_key):
                        summary.skipped_count += 1
                        print(f"  [skip] {sol_no}")
                        continue

                    state.mark_processed(dedup_key, request_id="dashboard", title=tender.get("solicitation_title"), link=link)
                    summary.new_count += 1
                    summary.new_tenders.append(tender)
                    print(f"  [new]  [{sol_no}] {tender.get('solicitation_title', '')[:60]}")

                    # Save state every 5 new tenders — partial progress survives timeouts
                    if summary.new_count % 5 == 0:
                        state.save()
        except Exception as exc:
            print(f"\nRun error: {exc}")
            summary.error_count += 1
            summary.errors.append(str(exc))
        finally:
            # Always save state and record the run — even on timeout/interrupt
            state.save()
            workflow_start = os.environ.get("WORKFLOW_START")
            if workflow_start:
                summary.duration_seconds = time.time() - float(workflow_start)
            else:
                summary.duration_seconds = time.monotonic() - start
            dashboard_data.record_run(summary, data_dir=Path("data"))
            status = "interrupted" if interrupted else "complete"
            print(f"\n{status.title()}. New: {summary.new_count} | Skipped: {summary.skipped_count} | Errors: {summary.error_count}")
        return

    from config import Config
    config = Config.load()

    if args.visible:
        config.scraper.headless = False
    if args.weekly:
        from scraper import WEEKLY_URL
        config.scraper.search_url = WEEKLY_URL
    if args.pages:
        config.scraper.max_pages = args.pages

    if args.dry_run:
        import test_run
        from scraper import WEEKLY_URL
        await test_run.dry_run(
            limit=args.limit or 5,
            fetch_detail=not args.no_detail,
            headless=not args.visible,
            search_url=WEEKLY_URL if args.weekly else None,
        )
        return

    import agent
    await agent.run_agent()

if __name__ == "__main__":
    asyncio.run(main())
