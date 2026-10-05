#!/usr/bin/env python3
"""
Phase 0 dry-run for supplier matching — measures whether SQL-only retrieval
can actually find suppliers, before any of the feature is built.

Under the v1 design (docs/SCOPE_SUPPLIER_MATCHING.md) there is no AI ranking,
so match quality is entirely determined by `rfp_categories.keywords` coverage.
That column has never been read by production code, so its usefulness is
unknown. This script answers three questions against real data:

  1. How many accepted tenders match at least one category keyword?
  2. How many reach a usable shortlist (>= 3 candidate vendors)?
  3. How many of the matched vendors actually have a contact email?

READ-ONLY. Runs SELECT statements only — no writes, no schema changes.

Usage:
    python tools/match_coverage_dryrun.py            # summary
    python tools/match_coverage_dryrun.py --detail   # per-tender breakdown
    python tools/match_coverage_dryrun.py --limit 50 # cap tenders examined
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

# Scoring weights — mirror of the production design, kept here so the dry-run
# measures the same thing the feature will do.
PRODUCT_MATCH_POINTS = 3
CATEGORY_MATCH_POINTS = 1
BID_HISTORY_BONUS = 1

USABLE_SHORTLIST = 3   # candidates needed before a result is worth showing
SHORTLIST_SIZE = 5     # what the UI pre-checks


def tender_text(t: dict) -> str:
    """The haystack we search for category keywords."""
    parts = [
        t.get("solicitation_title") or "",
        t.get("gsin_description") or "",
        t.get("summary_of_contract") or "",
        t.get("requirements") or "",
    ]
    return " ".join(parts).lower()


def match_categories(text: str, categories: list[dict]) -> list[tuple[str, list[str]]]:
    """Categories whose keywords appear in the tender text.

    Returns (category, [keywords that hit]) so the rationale can name them —
    the production feature shows this verbatim to the associate.
    """
    hits = []
    for cat in categories:
        matched = [
            kw for kw in (cat.get("keywords") or [])
            if kw.strip() and re.search(rf"\b{re.escape(kw.strip().lower())}", text)
        ]
        if matched:
            hits.append((cat["category"], matched))
    return hits


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--detail", action="store_true", help="per-tender breakdown")
    ap.add_argument("--limit", type=int, default=None, help="max tenders to examine")
    args = ap.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set — add it to .env or the environment.")
        return 1

    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        categories = [dict(r) for r in await conn.fetch(
            "SELECT category, keywords FROM rfp_categories ORDER BY category"
        )]
        vendors = [dict(r) for r in await conn.fetch(
            "SELECT id, company, rfp_categories, emails, bid_count FROM vendors"
        )]
        products = [dict(r) for r in await conn.fetch(
            "SELECT vendor_id, vendor_company, product FROM products_by_vendor"
        )]
        sql = "SELECT * FROM tenders WHERE status IN ('accepted','submitted') ORDER BY id DESC"
        if args.limit:
            sql += f" LIMIT {int(args.limit)}"
        tenders = [dict(r) for r in await conn.fetch(sql)]
    finally:
        await conn.close()

    print("=" * 72)
    print("SUPPLIER MATCHING — PHASE 0 COVERAGE DRY-RUN   (read-only)")
    print("=" * 72)
    print(f"  accepted/submitted tenders : {len(tenders)}")
    print(f"  vendors                    : {len(vendors)}")
    print(f"  rfp categories             : {len(categories)}")
    print(f"  product rows               : {len(products)}")

    # ── Input-side health: can the data support matching at all? ───────────
    cats_with_kw = [c for c in categories if c.get("keywords")]
    kw_total = sum(len(c.get("keywords") or []) for c in categories)
    vendors_with_cat = [v for v in vendors if v.get("rfp_categories")]
    vendors_with_email = [v for v in vendors if v.get("emails")]

    print("\n--- INPUT HEALTH " + "-" * 55)
    print(f"  categories carrying keywords : {len(cats_with_kw)}/{len(categories)}"
          f" ({pct(len(cats_with_kw), len(categories))})   total keywords: {kw_total}")
    print(f"  vendors tagged to a category : {len(vendors_with_cat)}/{len(vendors)}"
          f" ({pct(len(vendors_with_cat), len(vendors))})")
    print(f"  vendors with a contact email : {len(vendors_with_email)}/{len(vendors)}"
          f" ({pct(len(vendors_with_email), len(vendors))})")

    if not cats_with_kw:
        print("\n  STOP: no category has keywords. SQL-only matching cannot work")
        print("        until keywords are populated. This is a data task.")
        return 2

    # ── Per-tender retrieval simulation ────────────────────────────────────
    products_by_vendor: dict[int, list[str]] = {}
    for p in products:
        products_by_vendor.setdefault(p["vendor_id"], []).append(p["product"])

    matched_any = usable = contactable = 0
    shortlist_sizes: list[int] = []
    category_use: Counter = Counter()
    unmatched: list[str] = []

    for t in tenders:
        text = tender_text(t)
        cat_hits = match_categories(text, categories)
        cat_names = {c for c, _ in cat_hits}
        for c in cat_names:
            category_use[c] += 1

        scored = []
        for v in vendors:
            vcats = set(v.get("rfp_categories") or [])
            overlap = vcats & cat_names
            prod_hits = [
                prod for prod in products_by_vendor.get(v["id"], [])
                if prod and prod.strip() and prod.strip().lower() in text
            ]
            if not overlap and not prod_hits:
                continue
            score = (len(prod_hits) * PRODUCT_MATCH_POINTS
                     + len(overlap) * CATEGORY_MATCH_POINTS
                     + (BID_HISTORY_BONUS if (v.get("bid_count") or 0) > 0 else 0))
            scored.append((score, v, sorted(overlap), prod_hits))

        scored.sort(key=lambda r: (-r[0], r[1]["company"].lower()))
        shortlist_sizes.append(len(scored))

        if scored:
            matched_any += 1
        if len(scored) >= USABLE_SHORTLIST:
            usable += 1
        if any(v.get("emails") for _, v, _, _ in scored[:SHORTLIST_SIZE]):
            contactable += 1
        if not scored:
            unmatched.append(f"[{t.get('solicitation_no','?')}] "
                             f"{(t.get('solicitation_title') or '')[:58]}")

        if args.detail:
            print(f"\n  [{t.get('solicitation_no','?')}] "
                  f"{(t.get('solicitation_title') or '')[:58]}")
            print(f"     categories hit : {', '.join(sorted(cat_names)) or '(none)'}")
            print(f"     candidates     : {len(scored)}")
            for score, v, overlap, prod_hits in scored[:SHORTLIST_SIZE]:
                bits = []
                if overlap:
                    bits.append(f"category: {', '.join(overlap)}")
                if prod_hits:
                    bits.append(f"products: {', '.join(prod_hits[:3])}")
                if (v.get("bid_count") or 0) > 0:
                    bits.append(f"{v['bid_count']} prior bids")
                mail = "✉" if v.get("emails") else "no email"
                print(f"       {score:>3}pts  {v['company'][:34]:34} {mail:9} "
                      f"{'; '.join(bits)[:60]}")

    # ── Verdict ────────────────────────────────────────────────────────────
    n = len(tenders) or 1
    median = sorted(shortlist_sizes)[len(shortlist_sizes) // 2] if shortlist_sizes else 0

    print("\n--- RESULTS " + "-" * 60)
    print(f"  tenders matching >=1 vendor        : {matched_any}/{n} ({pct(matched_any, n)})")
    print(f"  tenders with a usable shortlist>={USABLE_SHORTLIST}  : {usable}/{n} ({pct(usable, n)})")
    print(f"  tenders with a contactable top {SHORTLIST_SIZE}    : {contactable}/{n} ({pct(contactable, n)})")
    print(f"  median candidates per tender       : {median}")

    if category_use:
        print("\n  most-used categories:")
        for cat, cnt in category_use.most_common(8):
            print(f"    {cnt:>4}x  {cat}")
    idle = [c["category"] for c in cats_with_kw if c["category"] not in category_use]
    if idle:
        print(f"\n  categories that never matched ({len(idle)}): "
              f"{', '.join(idle[:10])}{' ...' if len(idle) > 10 else ''}")
    if unmatched:
        print(f"\n  tenders with NO match ({len(unmatched)}):")
        for line in unmatched[:12]:
            print(f"    {line}")
        if len(unmatched) > 12:
            print(f"    ... and {len(unmatched) - 12} more")

    rate = usable / n
    print("\n" + "=" * 72)
    if rate >= 0.6:
        print(f"VERDICT: GO — {pct(usable, n)} of tenders reach a usable shortlist.")
        print("         SQL-only matching is viable. Proceed to Phase 1.")
    elif rate >= 0.3:
        print(f"VERDICT: PARTIAL — only {pct(usable, n)} reach a usable shortlist.")
        print("         Worth building, but enrich keywords first using the")
        print("         'never matched' and 'NO match' lists above.")
    else:
        print(f"VERDICT: NOT YET — {pct(usable, n)} reach a usable shortlist.")
        print("         Keyword coverage is the blocker, not the code. Enrich")
        print("         keywords and re-run before committing engineering time.")
    print("=" * 72)
    return 0


def pct(a: int, b: int) -> str:
    return f"{(100 * a / b):.0f}%" if b else "0%"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
