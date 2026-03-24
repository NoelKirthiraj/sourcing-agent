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
    if args.weekly:
        from scraper import WEEKLY_URL
        config.scraper.search_url = WEEKLY_URL
    if args.pages:
        config.scraper.max_pages = args.pages

    state_path = Path("processed_solicitations.json")
    if args.reset_state and state_path.exists():
        state_path.unlink()
        print("State reset — all solicitations will be reprocessed")

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
