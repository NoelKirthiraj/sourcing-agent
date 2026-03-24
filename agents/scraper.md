# Scraper Agent

## Responsibility

Operates a headless Chromium browser session to navigate CanadaBuys, paginate through all result pages, and extract the 11 tender fields from both listing and detail pages.

## Key Files

```
scraper.py               # All portal interaction — ScraperConfig + CanadaBuysScraper
tests/unit/
  test_scraper_helpers.py  # _clean(), _absolute() utility tests
tests/integration/
  test_scraper_extraction.py  # Playwright with fixture HTML
tests/fixtures/
  canadabuys_listing.html     # Captured listing page — update when selectors break
  canadabuys_detail.html      # Captured detail page — update when selectors break
```

## Commands

```bash
# Validate scraper against live portal (no CFlow writes)
python run.py --dry-run --limit 5

# Watch the browser navigate — use when 0 results returned
python run.py --dry-run --visible --limit 1

# Run scraper unit + integration tests (no live portal needed)
pytest tests/unit/test_scraper_helpers.py tests/integration/test_scraper_extraction.py -v

# Capture fresh HTML fixtures after a portal HTML change
python -c "
import asyncio
from playwright.async_api import async_playwright

async def capture():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto('https://canadabuys.canada.ca/en/tender-opportunities?search_filter=&pub%5B1%5D=1&status%5B87%5D=87&category%5B153%5D=153&category%5B154%5D=154&category%5B156%5D=156&Apply_filters=Apply+filters&record_per_page=50&current_tab=t&words=', wait_until='networkidle')
        open('tests/fixtures/canadabuys_listing.html','w').write(await page.content())
        print('Listing captured. Now visit a tender detail page in the browser.')
        input('Paste a tender detail URL and press Enter: ')
        await browser.close()

asyncio.run(capture())
"
```

## Verification

After changing CSS selectors in `scraper.py`:

1. Run `pytest tests/integration/test_scraper_extraction.py -v` — fixtures must still pass
2. Run `python run.py --dry-run --limit 3` — live portal must return ≥1 tender with `solicitation_no` and `solicitation_title` non-empty
3. Confirm `inquiry_link` values are absolute URLs starting with `https://canadabuys.canada.ca`
4. If `contact_email` is empty on all tenders — check detail page selector; some tenders legitimately have no contact info (acceptable)

## Patterns

### Correct — always await `networkidle` before extracting

```python
await page.goto(url, timeout=self.config.timeout_ms, wait_until="networkidle")
await page.wait_for_selector("table.views-table, .view-content", timeout=self.config.timeout_ms)
tenders = await self._extract_listing(page)
```

### Wrong — extracting before JS renders

```python
await page.goto(url)          # No wait_until — results table may be empty
tenders = await self._extract_listing(page)
```

### Correct — fallback selector chains for resilience

```python
title_el = await row.query_selector(
    "h3 a, .title a, td.views-field-title a"  # try 3 selectors
)
```

### Wrong — single brittle selector

```python
title_el = await row.query_selector("h3 a")  # breaks if markup changes
```

### Correct — graceful missing field

```python
tender["contact_email"] = (await email_el.inner_text()).strip() if email_el else ""
```

### Wrong — assuming element exists

```python
tender["contact_email"] = (await email_el.inner_text()).strip()  # AttributeError if None
```

## Common Mistakes

- **Don't** open a new browser instance per tender detail page.
  **Do** reuse `self._browser` — call `await self._browser.new_page()`, scrape, then `await page.close()`.

- **Don't** scrape detail pages for tenders that are already in state (duplicates).
  **Do** check `state.already_processed()` in the orchestrator *before* calling `fetch_tender_detail()` — saves N detail page loads per run.

- **Don't** ignore the `max_pages` cap.
  **Do** always respect it — without it, a misconfigured filter could paginate indefinitely.

- **Don't** update fixture HTML files without also verifying all scraper tests still pass.
  **Do** run `pytest tests/integration/test_scraper_extraction.py` immediately after updating fixtures.

- **Don't** use `page.content()` for extraction — it returns the initial HTML before JS runs.
  **Do** use `page.query_selector()` after `networkidle` — operates on the live rendered DOM.

## CSS Selector Reference

These are the current selectors for CanadaBuys. Update this table whenever selectors change.

| Field | Primary Selector | Fallback Selector | Source |
|-------|-----------------|-------------------|--------|
| Title + link | `h3 a` | `.title a`, `td.views-field-title a` | Listing |
| Solicitation No | `[class*='solicitation-number']` | `td.views-field-field-solicitation-number` | Listing |
| Client | `[class*='organization']` | `td.views-field-field-organization` | Listing |
| Closing Date | `[class*='closing-date'] time` | `td.views-field-field-tender-closing-date` | Listing |
| GSIN Description | `[class*='gsin'] .field__item` | `.field--name-field-gsin .field__item` | Detail |
| Contact block | `.field--name-field-contact` | `[class*='contact-information']` | Detail |
| Contact email | `a[href^='mailto:']` | *(within contact block)* | Detail |
| Contact phone | `[class*='phone']` | `[class*='telephone']` | Detail |
| Time & Zone | `[class*='timezone']` | `[class*='time-zone']` | Detail |
| Notifications | `[class*='amendment']` | `[class*='notification']` | Detail |

## Dependencies

- **Depends on:** `config.py` (ScraperConfig)
- **Depended on by:** `agent.py` (calls `fetch_tender_list()` and `fetch_tender_detail()`)
- **Test dependency:** `tests/fixtures/*.html` — must be re-captured after portal HTML changes
