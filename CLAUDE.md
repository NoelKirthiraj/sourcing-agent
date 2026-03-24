# CanadaBuys → CFlow Sourcing Intake Agent

A scheduled Python agent that scrapes federal IT tender opportunities from CanadaBuys daily and automatically creates CFlow sourcing workflow requests — replacing a manual Data Miner + form-fill process. Runs on GitHub Actions cron at 7 AM ET weekdays, costs $0/month.

## Commands

```bash
# Dry-run — scrape portal, print payloads, no CFlow records created
python run.py --dry-run --limit 5

# Dry-run with visible browser (debug selector issues)
python run.py --dry-run --visible --limit 1

# Discover CFlow form field names (run before first live submission)
python run.py --discover-fields

# Full production run
python run.py

# Run tests (no live portal or CFlow needed)
pytest tests/ -v --tb=short

# Unit tests only (fastest — ~5 seconds)
pytest tests/unit/ -v

# Reset dedup state and reprocess all tenders
python run.py --reset-state
```

## Verification

After any change, verify in this order:

1. **Unit tests pass:** `pytest tests/unit/` — should complete in <10 seconds with no failures
2. **Dry-run returns tenders:** `python run.py --dry-run --limit 3` — should print ≥1 tender with all 11 fields populated; no tracebacks
3. **CFlow payload is correct:** Check printed JSON — `solicitation_no` looks like `PW-EZZ-*` or `WS-*`; `inquiry_link` is an absolute `https://canadabuys.canada.ca/...` URL
4. **Deduplication works:** Run dry-run twice — second run should log `Skipped: N` for tenders from first run

For CFlow integration changes specifically: set `CFLOW_SUBMIT_NOW=false`, run `python run.py --limit 2`, verify draft records in CFlow UI, then delete them.

## Common Mistakes

- **Don't** call `state.save()` inside the per-tender loop.
  **Do** call it once after all tenders are processed — if the run crashes mid-batch, unconfirmed tenders must retry next run.

- **Don't** mark a solicitation as processed before the CFlow POST succeeds.
  **Do** call `state.mark_processed()` only after receiving a `200`/`201` from CFlow.

- **Don't** use `requests` or `httpx` to fetch CanadaBuys pages directly.
  **Do** always use Playwright — the portal is JS-rendered and blocks raw HTTP scrapers via robots.txt.

- **Don't** add `None` values to the CFlow payload dict.
  **Do** use `tender.get("field", "")` — CFlow rejects null field values with a 422.

- **Don't** hardcode credentials or the CFlow workflow name anywhere in source files.
  **Do** always read from environment variables via `config.py`.

- **Don't** raise an exception when a single tender's CFlow submission fails.
  **Do** catch it, log it, increment `summary.error_count`, and continue to the next tender.

- **Don't** assume `scraper._extract_detail()` returns all 11 fields — contact info is sometimes absent on the portal.
  **Do** always use `.get("field", "")` when merging detail into the tender dict.

- **Don't** modify `cflow_client._build_payload()` to use display labels if the CFlow API requires API key names.
  **Do** run `discover_fields.py` first — use whatever key the API returns, not what you see in the CFlow UI.

## Project Structure

```
agent.py              # Orchestrator — start here when tracing a bug
scraper.py            # Playwright browser — all portal interaction lives here
cflow_client.py       # CFlow REST — _build_payload() is the field mapping
state.py              # JSON deduplication — keyed on solicitation_no
notifier.py           # Slack + email summaries
config.py             # Env var loader — fails fast if required vars missing
run.py                # CLI flags: --dry-run --visible --discover-fields --reset-state
tests/
  unit/               # Fast, no network, no browser
  integration/        # Mocked CFlow (respx) + mocked portal HTML (Playwright route())
  fixtures/           # Captured CanadaBuys HTML — update when selectors change
.github/workflows/
  daily_agent.yml     # Cron + secrets injection + state cache + log artifact
```

## Tech Stack

- **Playwright** (not Selenium, not requests): CanadaBuys is JS-rendered and blocks raw HTTP
- **HTTPX** (not requests): async-native; matches the `async/await` pattern throughout
- **JSON file** (not SQLite/Postgres): dedup state is a simple string set; no query needed
- **GitHub Actions** (not Heroku/Railway/VPS): $0/month; ~132 min/month vs 2,000 min free tier

## Sub-agent Files

- [agents/scraper.md](agents/scraper.md) — Playwright selectors, pagination, fixture workflow
- [agents/cflow.md](agents/cflow.md) — CFlow REST API, field mapping, auth headers
- [agents/orchestrator.md](agents/orchestrator.md) — Agent loop, error handling, state lifecycle
