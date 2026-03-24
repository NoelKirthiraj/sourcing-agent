# Testing Strategy: CanadaBuys → CFlow Sourcing Intake Agent

## Overview

This is a backend pipeline with no UI and no database. Testing focuses on three areas: unit tests for the pure logic functions (field extraction, deduplication, payload mapping), integration tests that mock external dependencies (CanadaBuys portal, CFlow API), and a single E2E validation path using the built-in `--dry-run` tooling rather than a separate E2E framework. The goal is confidence that new tenders are correctly scraped, deduplicated, mapped, and submitted — not 100% coverage.

**Test runner:** `pytest` + `pytest-asyncio`  
**Mocking:** `pytest-mock` + `respx` (async HTTP mock for HTTPX)  
**Browser mocking:** Playwright's `route()` API for intercepting portal requests  

---

## Testing Pyramid

```
          /    E2E     \         ← 1 scenario: full dry-run against live portal
         /--------------\           (manual, pre-deploy gate only)
        /  Integration   \       ← Mocked CFlow API + mocked portal HTML
       /------------------\
      /    Unit Tests      \     ← Pure logic: dedup, mapping, field parsing,
     /----------------------\       config validation, notifier formatting
```

**MVP allocation:** 65% unit · 30% integration · 5% E2E (manual)

---

## Unit Tests

### `state.py` — Deduplication Logic

| Test | Description | Priority |
|------|-------------|----------|
| `test_new_solicitation_is_not_processed` | `already_processed("PW-NEW-001")` returns `False` on empty state | P0 |
| `test_mark_then_check_returns_true` | After `mark_processed("PW-123")`, `already_processed("PW-123")` returns `True` | P0 |
| `test_state_persists_after_save_reload` | `mark_processed` + `save()` + reload from disk → same entries present | P0 |
| `test_different_solicitation_not_affected` | Marking "PW-AAA" does not affect "PW-BBB" | P0 |
| `test_corrupt_json_starts_fresh` | Malformed JSON in state file → logs warning, returns empty state (no exception) | P1 |
| `test_missing_state_file_starts_fresh` | Non-existent file path → empty state, no exception | P1 |
| `test_mark_processed_stores_request_id` | `mark_processed("PW-123", request_id="REQ-9")` → state dict contains `cflow_request_id: "REQ-9"` | P1 |
| `test_mark_processed_stores_timestamp` | `processed_at` field is ISO 8601 UTC format | P1 |

```python
# Example test
def test_mark_then_check_returns_true(tmp_path):
    state = AgentState(path=tmp_path / "state.json")
    assert state.already_processed("PW-123") is False
    state.mark_processed("PW-123", request_id="REQ-9", title="Test Tender")
    assert state.already_processed("PW-123") is True
```

---

### `cflow_client.py` — Payload Mapping

| Test | Description | Priority |
|------|-------------|----------|
| `test_build_payload_maps_all_11_fields` | Full tender dict → payload contains all 11 expected form field keys | P0 |
| `test_build_payload_includes_source_field` | `Source` key equals `"CanadaBuys Auto-Agent"` | P0 |
| `test_build_payload_includes_workflow_name` | `workflow_name` matches `CFlowConfig.workflow_name` | P0 |
| `test_build_payload_handles_missing_fields` | Tender dict with missing keys → payload uses empty strings (no `KeyError`) | P0 |
| `test_build_payload_no_none_values` | No `None` values in payload — CFlow API rejects null fields | P1 |
| `test_build_payload_strips_whitespace` | Fields with leading/trailing spaces → stripped in payload | P1 |

```python
# Example test
def test_build_payload_maps_all_11_fields():
    config = CFlowConfig(
        base_url="https://us.cflowapps.com",
        api_key="k", user_key="u", username="u@test.com",
        workflow_name="Sourcing Workflow"
    )
    client = CFlowClient(config)
    tender = {
        "solicitation_title": "IT Security Services",
        "solicitation_no": "PW-EZZ-001",
        "gsin_description": "EDP Services",
        "inquiry_link": "https://canadabuys.canada.ca/...",
        "closing_date": "2026-04-15",
        "time_and_zone": "14:00 Eastern",
        "notifications": "0 amendments",
        "client": "Shared Services Canada",
        "contact_name": "Jane Smith",
        "contact_email": "jane@ssc.gc.ca",
        "contact_phone": "613-555-0100",
    }
    payload = client._build_payload(tender)
    fields = payload["form_fields"]
    assert fields["Solicitation Title"] == "IT Security Services"
    assert fields["Solicitation No"] == "PW-EZZ-001"
    assert fields["GSIN Description"] == "EDP Services"
    assert fields["Contact Email"] == "jane@ssc.gc.ca"
    assert fields["Source"] == "CanadaBuys Auto-Agent"
    assert payload["workflow_name"] == "Sourcing Workflow"
```

---

### `scraper.py` — Field Extraction Helpers

| Test | Description | Priority |
|------|-------------|----------|
| `test_clean_strips_whitespace` | `_clean("  hello  world  ")` → `"hello world"` | P0 |
| `test_clean_handles_none` | `_clean(None)` → `""` (no exception) | P0 |
| `test_clean_collapses_newlines` | `_clean("line1\n  line2")` → `"line1 line2"` | P0 |
| `test_absolute_url_with_relative_path` | `_absolute("/en/tender/123")` → `"https://canadabuys.canada.ca/en/tender/123"` | P0 |
| `test_absolute_url_already_absolute` | `_absolute("https://example.com/foo")` → unchanged | P1 |
| `test_absolute_url_with_none` | `_absolute(None)` → `""` | P1 |
| `test_clean_html_strips_tags` | `_clean_html("<b>bold</b>")` → `" bold "` | P1 |
| `test_clean_html_decodes_entities` | `_clean_html("&amp;")` → `"&"` | P1 |
| `test_capture_extracts_group` | `_capture("Foo 123 Bar", r"Foo\s+(\d+)")` → `"123"` | P0 |
| `test_capture_returns_empty_on_no_match` | `_capture("no match", r"xyz")` → `""` | P0 |

---

### `config.py` — Validation

| Test | Description | Priority |
|------|-------------|----------|
| `test_missing_required_var_raises` | Missing `CFLOW_API_KEY` → `EnvironmentError` with key name in message | P0 |
| `test_all_vars_present_returns_config` | All 5 required vars set → `Config` object returned with correct values | P0 |
| `test_bool_env_true_values` | `"true"`, `"1"`, `"yes"` all parse as `True` | P1 |
| `test_bool_env_false_values` | `"false"`, `"0"`, `"no"` all parse as `False` | P1 |
| `test_optional_vars_have_defaults` | `CFLOW_SUBMIT_NOW` absent → defaults to `True` | P1 |

---

### `notifier.py` — Message Formatting

| Test | Description | Priority |
|------|-------------|----------|
| `test_slack_blocks_contain_counts` | `RunSummary(new_count=3, skipped=10, errors=0)` → Slack payload contains "3" and "10" | P1 |
| `test_slack_includes_tender_links` | Summary with 2 new tenders → block text contains both `inquiry_link` URLs | P1 |
| `test_slack_marks_error_when_nonzero` | `error_count=2` → header emoji is `⚠️` not `✅` | P1 |
| `test_email_subject_reflects_new_count` | `new_count=5` → subject contains "5 new tender(s)" | P1 |
| `test_email_subject_zero_new` | `new_count=0` → subject contains "No new tenders" | P1 |

---

## Integration Tests

These tests mock external HTTP calls using `respx` (for CFlow) and Playwright's `route()` API (for CanadaBuys). No live network calls in CI.

### CFlow API Integration

| Test | Description | Priority |
|------|-------------|----------|
| `test_create_request_success_returns_id` | Mock CFlow returns `201 {"request_id": "REQ-42"}` → `create_sourcing_request()` returns `"REQ-42"` | P0 |
| `test_create_request_401_raises_runtime_error` | Mock CFlow returns `401` → `RuntimeError` raised with status code in message | P0 |
| `test_create_request_422_raises_with_body` | Mock CFlow returns `422 {"error": "Unknown field"}` → `RuntimeError` message includes response body | P0 |
| `test_create_request_500_raises` | Mock CFlow returns `500` → `RuntimeError` raised | P0 |
| `test_draft_mode_posts_to_draft_endpoint` | `submit_immediately=False` → POST goes to `/api/v1/requests/draft` not `/api/v1/requests` | P1 |
| `test_auth_headers_present` | Every request includes `api-key`, `user-key`, `username` headers | P1 |
| `test_timeout_handled_gracefully` | Mock CFlow times out → `RuntimeError` (not unhandled exception) | P1 |

```python
# Example integration test with respx
import respx, httpx, pytest

@pytest.mark.asyncio
async def test_create_request_success_returns_id():
    config = CFlowConfig(
        base_url="https://us.cflowapps.com",
        api_key="k", user_key="u", username="u@test.com",
        workflow_name="Sourcing Workflow"
    )
    with respx.mock:
        respx.post("https://us.cflowapps.com/api/v1/requests").mock(
            return_value=httpx.Response(201, json={"request_id": "REQ-42"})
        )
        client = CFlowClient(config)
        result = await client.create_sourcing_request({
            "solicitation_no": "PW-001",
            "solicitation_title": "Test",
            # ... other fields
        })
    assert result == "REQ-42"
```

---

### Orchestrator Integration (`agent.py`)

| Test | Description | Priority |
|------|-------------|----------|
| `test_agent_skips_duplicate_solicitations` | State pre-loaded with dedup key → agent processes 0 new, skipped=1 | P0 |
| `test_agent_marks_state_on_success` | New tender submitted successfully → `state.already_processed(dedup_key)` returns `True` after run | P0 |
| `test_agent_does_not_mark_state_on_cflow_failure` | CFlow returns 500 → dedup key NOT in state → retried next run | P0 |
| `test_agent_uses_link_as_fallback_dedup_key` | Tender with empty solicitation_no → dedup key is `inquiry_link` URL | P0 |
| `test_agent_fetches_detail_before_dedup` | Detail page is fetched before checking state (sol_no comes from detail) | P0 |
| `test_agent_auto_detects_saturday` | When `weekday() == 5` → uses `WEEKLY_URL` search filter | P1 |
| `test_agent_continues_after_single_failure` | 3 tenders; second CFlow call fails → 2 successes, 1 error, run completes | P0 |
| `test_agent_sends_summary_notification` | Run completes → `notifier.send()` called once with correct `RunSummary` counts | P1 |
| `test_agent_saves_state_after_run` | `state.save()` called exactly once at end of run | P1 |

---

### Scraper Integration (Mocked Portal HTML)

These use Playwright's `page.route()` to serve fixture HTML files instead of hitting the live portal.

| Test | Description | Priority |
|------|-------------|----------|
| `test_extract_listing_returns_titles_and_links` | Fixture HTML with tender links → returns dicts with `solicitation_title` and `inquiry_link` | P0 |
| `test_extract_listing_builds_absolute_links` | Relative href in fixture → `inquiry_link` is absolute URL | P0 |
| `test_extract_listing_deduplicates_across_calls` | Same page called twice with shared `seen` set → no duplicates | P0 |
| `test_extract_detail_returns_contact_email` | Detail page text with "Email" line → `contact_email` extracted correctly via regex | P0 |
| `test_extract_detail_returns_gsin` | Detail page text after "Related notices" → `gsin_description` populated | P0 |
| `test_extract_detail_returns_closing_date` | Detail page text with "Closing date and time" → `closing_date` extracted | P0 |
| `test_pagination_clicks_next` | Fixture page 1 has `rel="next"` link → click navigates to page 2 | P1 |
| `test_pagination_stops_at_max_pages` | `max_pages=2`, portal has 3 pages → only 2 pages fetched | P1 |
| `test_empty_results_page_returns_empty_list` | Fixture HTML with no tender links → returns `[]` (no exception) | P1 |
| `test_listing_regex_fallback` | Primary locator finds 0 links, regex fallback extracts from HTML | P1 |

```python
# Example scraper integration test with mocked portal
@pytest.mark.asyncio
async def test_extract_listing_returns_title_and_sol_no():
    listing_html = Path("tests/fixtures/canadabuys_listing.html").read_text()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("**/*", lambda r: r.fulfill(
            status=200,
            content_type="text/html",
            body=listing_html
        ))
        await page.goto("https://canadabuys.canada.ca/fake")

        scraper = CanadaBuysScraper(ScraperConfig())
        scraper._browser = browser
        seen = set()
        tenders = await scraper._extract_listing(page, seen)

        assert len(tenders) >= 1
        assert tenders[0]["solicitation_title"]  # non-empty
        assert tenders[0]["inquiry_link"].startswith("https://canadabuys.canada.ca")
        await browser.close()
```

---

## E2E Tests (Manual Pre-Deploy Gate)

There is one E2E scenario. It is **not automated in CI** — it is a manual gate run by the developer before each production deployment using the built-in `--dry-run` tooling. Automating it in CI would require the live CanadaBuys portal to be available and stable, creating a flaky external dependency.

### Scenario: Full Pipeline Dry-Run Against Live Portal

**Command:** `python run.py --dry-run --limit 5`

**Given:**
- Valid `.env` with CFlow credentials
- CanadaBuys portal is accessible
- `processed_solicitations.json` is empty or does not exist

**Steps:**
1. Agent scrapes live CanadaBuys portal with configured filters
2. Agent fetches detail pages for first 5 tenders
3. Agent prints JSON payloads that would be sent to CFlow
4. No CFlow records created

**Pass criteria:**
- [ ] At least 1 tender returned (portal accessible, filters working)
- [ ] All 11 fields present in printed payload (no empty required fields)
- [ ] `inquiry_link` values are valid absolute URLs
- [ ] `solicitation_no` values follow expected government format (e.g. `PW-`, `WS-`, `EN-`)
- [ ] `contact_email` contains `@` where populated
- [ ] No Python exceptions or tracebacks in output
- [ ] Run completes within 3 minutes

**If any criterion fails:** Debug with `--dry-run --visible` to watch browser navigate portal, then fix CSS selectors in `scraper.py`.

---

## Test File Structure

```
tests/
├── conftest.py                    # Shared fixtures (tmp_path state, mock configs)
├── fixtures/
│   ├── canadabuys_listing.html    # Captured listing page HTML for scraper tests
│   ├── canadabuys_detail.html     # Captured detail page HTML for scraper tests
│   └── canadabuys_listing_p2.html # Page 2 for pagination tests
├── unit/
│   ├── test_state.py              # AgentState deduplication tests
│   ├── test_cflow_client.py       # Payload mapping tests
│   ├── test_scraper_helpers.py    # _clean(), _absolute() utility tests
│   ├── test_config.py             # Config validation tests
│   └── test_notifier.py           # Message formatting tests
└── integration/
    ├── test_cflow_api.py          # Mocked CFlow REST API tests (respx)
    ├── test_agent_orchestrator.py # Mocked end-to-end agent run tests
    └── test_scraper_extraction.py # Playwright with fixture HTML tests
```

---

## Tools & Configuration

```toml
# pyproject.toml additions
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-v --tb=short"

[tool.coverage.run]
source = ["agent", "scraper", "cflow_client", "state", "notifier", "config"]
omit = ["tests/*", "run.py", "discover_fields.py", "test_run.py"]
```

**Dependencies (add to `requirements.txt`):**
```
pytest>=8.0
pytest-asyncio>=0.23
pytest-mock>=3.12
respx>=0.21          # async HTTP mock for HTTPX
coverage>=7.0
```

**CI integration** (add to `.github/workflows/daily_agent.yml`):
```yaml
- name: Run tests
  run: pytest tests/ --tb=short -q
  # Note: integration tests that use Playwright require chromium
  # which is installed in the existing setup step
```

---

## What We're NOT Testing (MVP)

| Deferred | Reason |
|----------|--------|
| Live CanadaBuys portal in CI | Flaky external dependency; manual dry-run gate is sufficient |
| Live CFlow API in CI | Requires real credentials; creates real records; mock integration tests cover the contract |
| Notification delivery (Slack/SMTP) | Infrastructure concern; mock tests verify payload construction; delivery is Slack/SMTP's responsibility |
| CSS selector correctness | No fixture can guarantee parity with live portal; manual `--visible` dry-run is the right tool |
| GitHub Actions workflow YAML | Infrastructure-as-code; trust the platform |
| `discover_fields.py` | One-time setup tool; runs once per deployment; not part of the daily pipeline |
| Performance / load testing | Agent processes ~20–50 tenders/day; no scale concern at MVP |

---

## Tests Derived from P0 User Stories

### Story: "New tenders appear in CFlow each morning without manual action"

**Happy path:**
- Given: 5 new tenders on CanadaBuys, 0 in state
- When: Agent runs
- Then: 5 CFlow records created, state updated with all 5 solicitation numbers

**Error case — CFlow down:**
- Given: 5 new tenders, CFlow returns 500
- When: Agent runs
- Then: 0 records created, 5 errors in summary, state NOT updated (retried next run)

**Edge case — empty portal:**
- Given: No tenders match search filters
- When: Agent runs
- Then: Run completes with `total_found=0`, warning in summary, no exceptions

---

### Story: "All 11 fields pre-filled in CFlow form"

**Happy path:**
- Given: Tender with all fields present on listing + detail pages
- When: Payload built
- Then: All 11 keys present in `form_fields` dict, no empty required fields

**Edge case — contact info missing:**
- Given: Tender detail page has no contact block
- When: Detail scraped
- Then: `contact_name`, `contact_email`, `contact_phone` are `""` (empty string, not `None`)

---

### Story: "Duplicate tenders never submitted to CFlow"

**Happy path:**
- Given: Solicitation "PW-001" already in state file
- When: Agent encounters "PW-001" in scrape results
- Then: CFlow POST not called, `skipped_count` incremented

**Edge case — state file missing:**
- Given: `processed_solicitations.json` deleted
- When: Agent runs
- Then: All tenders treated as new (acceptable), state file recreated cleanly
