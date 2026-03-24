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

# Validate weekly filters (Open + Goods + Last 7 days)
python run.py --dry-run --weekly --limit 5

# Watch the browser navigate — use when 0 results returned
python run.py --dry-run --visible --limit 1

# Run scraper unit + integration tests (no live portal needed)
pytest tests/unit/test_scraper_helpers.py tests/integration/test_scraper_extraction.py -v

# Capture fresh HTML fixtures after a portal HTML change
python capture_fixtures.py
```

## Verification

After changing extraction logic in `scraper.py`:

1. Run `pytest tests/integration/test_scraper_extraction.py -v` — fixtures must still pass
2. Run `python run.py --dry-run --limit 3` — live portal must return ≥1 tender with `solicitation_no` and `solicitation_title` non-empty
3. Confirm `inquiry_link` values are absolute URLs starting with `https://canadabuys.canada.ca`
4. Confirm `closing_date`, `client`, and `contact_name` are populated (requires detail page + Contact tab click)
5. If `contact_email` is empty on all tenders — check that the Contact information tab click is working

## Patterns

### Correct — two-step page load, then wait for content

```python
await page.goto(url, timeout=self.config.timeout_ms, wait_until="domcontentloaded")
await page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)
await page.wait_for_selector("main a[href*='/en/tender-opportunities/tender-notice/']", timeout=self.config.timeout_ms)
```

### Wrong — extracting before JS renders

```python
await page.goto(url)          # No wait_until — results may be empty
tenders = await self._extract_listing(page, seen)
```

### Correct — link-based listing extraction with dedup

```python
links = page.locator(
    "main a[href*='/en/tender-opportunities/tender-notice/'], "
    "main a[href*='/en/tender-opportunities/award-notice/'], "
    "main a[href*='/en/tender-opportunities/contract-history/']"
)
```

### Wrong — CSS row selectors (no longer match portal markup)

```python
rows = await page.query_selector_all("article.tender-result, tr.odd, tr.even")  # broken
```

### Correct — click-based pagination

```python
await next_btn.click()
await page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)
```

### Wrong — goto with relative query string

```python
await page.goto(_absolute(next_href))  # urljoin drops path on ?query strings
```

### Correct — regex detail extraction on preserved newlines

```python
text = (await page.locator("body").inner_text()).strip()  # preserves \n
detail["closing_date"] = _capture(text, r"Closing date and time\s+([^\n]+)")
```

### Wrong — collapsing newlines before regex

```python
text = _clean(await page.locator("body").inner_text())  # \n → space, breaks [^\n]+
```

## Common Mistakes

- **Don't** open a new browser instance per tender detail page.
  **Do** reuse `self._context` — call `await self._context.new_page()`, scrape, then `await page.close()`.

- **Don't** use `self._browser.new_page()` directly — it bypasses the user-agent override.
  **Do** always use `self._context.new_page()` — the context has the Chrome user-agent that avoids 403 blocks.

- **Don't** ignore the `max_pages` cap.
  **Do** always respect it — without it, a misconfigured filter could paginate indefinitely.

- **Don't** update fixture HTML files without also verifying all scraper tests still pass.
  **Do** run `pytest tests/integration/test_scraper_extraction.py` immediately after updating fixtures.

- **Don't** forget to click the Contact information tab before extracting contact fields.
  **Do** extract header fields first (Description tab), then click Contact tab, then extract contact fields.

## Extraction Reference

The scraper uses two strategies: link-based locators for listings, regex on body text for detail pages.

### Listing Extraction

| What | Strategy |
|------|----------|
| Title + link | `page.locator("main a[href*='/en/tender-opportunities/tender-notice/']")` + award-notice + contract-history variants |
| Fallback | Regex on `page.content()` HTML for same href patterns |
| Dedup | `seen` set (by URL) shared across all pages in a single run |

### Detail Extraction (regex on `inner_text()`)

| Field | Regex Pattern | Notes |
|-------|--------------|-------|
| Solicitation No | `Solicitation number\s+([^\n]+)` | First line after header |
| GSIN Description | `Related notices\s+(.+?)\s+(?:Show more description\|Contract duration\|...)` | Anchors after tab labels, not "Description" |
| Closing Date | `Closing date and time\s+([^\n]+)` | Date only (e.g. `2026/04/10`) |
| Time and Zone | `Closing date and time\s+.*?(EDT\|EST\|...)` | Timezone abbreviation |
| Notifications | `Last amendment date\s+([^\n]+)` | Empty if no amendments |
| Client | `Organization\s+([^\n]+)` | After clicking Contact tab |
| Contact Name | `Contracting authority\s+([^\n]+)` | After clicking Contact tab |
| Contact Email | `Email\s+([A-Z0-9._%+-]+@...)` | After clicking Contact tab |
| Contact Phone | `Phone\s+([^\n]+)` | After clicking Contact tab |

### Search URL Filter Parameters

| Filter | Param | Used In |
|--------|-------|---------|
| Last 24 hours | `pub[1]=1` | Daily (default) |
| Last 7 days | `pub[2]=2` | Weekly (Saturdays) |
| Open | `status[87]=87` | Both |
| Goods | `category[153]=153` | Weekly only |

## Dependencies

- **Depends on:** `config.py` (ScraperConfig)
- **Depended on by:** `agent.py` (calls `fetch_tender_list()` and `fetch_tender_detail()`)
- **Test dependency:** `tests/fixtures/*.html` — must be re-captured after portal HTML changes
