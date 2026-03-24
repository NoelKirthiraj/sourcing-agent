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

async def dry_run(limit: int, fetch_detail: bool, headless: bool, search_url: str | None = None):
    log.info("DRY RUN — no CFlow records will be created")
    try:
        config = Config.load()
        scraper_config = config.scraper
        scraper_config.headless = headless
    except EnvironmentError:
        scraper_config = ScraperConfig(headless=headless)
        config = None
    if search_url:
        scraper_config.search_url = search_url

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
