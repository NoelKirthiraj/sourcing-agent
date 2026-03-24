# Orchestrator Agent

## Responsibility

Sequences the full pipeline — scrape → deduplicate → submit → notify — owns the error handling contract, run metrics, and state lifecycle.

## Key Files

```
agent.py                             # run_agent() — the full pipeline
state.py                             # AgentState — dedup memory
notifier.py                          # RunSummary + Slack/email dispatch
run.py                               # CLI flags (delegates to agent.py)
tests/unit/
  test_state.py                      # Deduplication logic tests
  test_notifier.py                   # Message formatting tests
tests/integration/
  test_agent_orchestrator.py         # Full mocked pipeline tests
```

## Commands

```bash
# Run the full pipeline
python run.py

# Run with visible browser (debug scraper issues)
python run.py --visible

# Limit to N tenders (useful for testing without processing all 50)
python run.py --pages 1

# Wipe state and reprocess all tenders
python run.py --reset-state

# Run orchestrator + state tests
pytest tests/unit/test_state.py tests/integration/test_agent_orchestrator.py -v
```

## Verification

After changing `agent.py`, `state.py`, or `notifier.py`:

1. `pytest tests/unit/test_state.py` — deduplication logic must pass
2. `pytest tests/integration/test_agent_orchestrator.py` — mocked pipeline must pass
3. Run `python run.py --dry-run --limit 3` — confirm summary printed at end with correct counts
4. Run `python run.py --dry-run --limit 3` a second time — `Skipped: 3`, `New: 0` (dedup working)
5. If notifications configured: confirm Slack/email received with correct counts

## Patterns

### Correct — fetch detail first, then dedup, per-tender try/except

```python
for tender in tenders:
    link = tender.get("inquiry_link", "").strip()
    if not link:
        continue

    # Fetch detail first — solicitation_no comes from the detail page
    try:
        detail = await scraper.fetch_tender_detail(link)
        tender.update(detail)
    except Exception as exc:
        log.error("Failed to fetch detail for %s: %s", link, exc)
        summary.error_count += 1
        continue

    sol_no = tender.get("solicitation_no", "").strip()
    dedup_key = sol_no or link  # fallback to URL if no solicitation number
    if state.already_processed(dedup_key):
        summary.skipped_count += 1
        continue

    try:
        request_id = await cflow.create_sourcing_request(tender)
        state.mark_processed(dedup_key, request_id=request_id, title=tender.get("solicitation_title"))
        summary.new_count += 1
        summary.new_tenders.append(tender)
    except Exception as exc:
        log.error("Failed %s: %s", sol_no, exc)
        summary.error_count += 1
        summary.errors.append(f"{sol_no}: {exc}")
        # DO NOT call state.mark_processed() here — let it retry next run
```

### Wrong — checking solicitation_no before fetching detail (it's empty from listing)

```python
sol_no = tender.get("solicitation_no", "").strip()
if not sol_no:
    continue  # Wrong — skips every tender since listing doesn't extract sol_no
```

### Wrong — marking state before CFlow confirms

```python
state.mark_processed(sol_no)              # Wrong — marks before POST succeeds
request_id = await cflow.create_sourcing_request(tender)  # If this raises, tender lost forever
```

### Correct — save state once, after all tenders processed

```python
# Outside the for loop:
state.save()
await notifier.send(summary)
```

### Wrong — saving inside the loop

```python
for tender in tenders:
    ...
    state.mark_processed(sol_no)
    state.save()    # Wrong — file I/O on every tender; also misleading partial state on crash
```

## State Lifecycle Rules

These rules are **invariants** — never violate them:

| Rule | Reason |
|------|--------|
| `mark_processed()` only called after CFlow returns 200/201 | Failed submissions must retry next run |
| `save()` called exactly once per run, after the loop | Atomic-style update; crash-safe |
| State file keyed on `solicitation_no` or `inquiry_link` | `solicitation_no` preferred; `inquiry_link` used as fallback when sol_no is empty |
| Corrupt state file → log warning + start fresh | Self-healing; worst case is one duplicate batch |
| `--reset-state` deletes file before run | Clean reprocessing when explicitly requested |

## RunSummary Fields

```python
@dataclass
class RunSummary:
    run_at: str          # ISO timestamp, set on init
    total_found: int     # All tenders on all pages
    new_count: int       # Successfully submitted to CFlow
    skipped_count: int   # Already in state file
    error_count: int     # CFlow submission failures
    new_tenders: list    # Tender dicts for new submissions (for notification links)
    errors: list         # Error strings for notification body
```

The notification is always sent — even when `new_count == 0`. The sourcing team must know the agent ran and found nothing, vs. the agent not running at all.

## Common Mistakes

- **Don't** call `await notifier.send()` inside a try/except that swallows errors silently.
  **Do** let notification errors log a warning but not affect run exit code — a failed notification doesn't mean the pipeline failed.

- **Don't** pass the raw exception object to `summary.errors`.
  **Do** pass `f"{sol_no}: {exc}"` — the solicitation number is essential for manual follow-up.

- **Don't** skip detail page fetch to save time in production.
  **Do** always fetch detail pages for new tenders — `contact_name`, `contact_email`, `contact_phone`, and `gsin_description` only appear on the detail page.

- **Don't** use `asyncio.gather()` to parallelize CFlow POSTs without rate-limit awareness.
  **Do** process tenders sequentially in the MVP — CFlow may throttle burst requests; sequential is safe and fast enough for ~50 tenders/day.

- **Don't** catch `KeyboardInterrupt` or `SystemExit` in the main try/except.
  **Do** let the process terminate naturally — GitHub Actions will mark the run as cancelled.

## GitHub Actions Integration

The orchestrator writes to `agent.log` using Python's `logging.FileHandler`. GitHub Actions uploads this as an artifact after every run (retained 30 days). Key log lines to monitor:

```
INFO  CanadaBuys → CFlow Agent starting  2026-03-24 12:00
INFO  Found 47 tender(s) total across all pages
INFO  ✓ CFlow request created: REQ-4821  (solicitation PW-EZZ-123)
INFO  Done. New: 3 | Already processed: 44 | Errors: 0 | Total seen: 47
```

If `Errors: N > 0` — check the log for the specific solicitation numbers and error messages.

## Dependencies

- **Depends on:** `scraper.py`, `cflow_client.py`, `state.py`, `notifier.py`, `config.py`
- **Depended on by:** `run.py` (CLI entrypoint), GitHub Actions (`daily_agent.yml`)
- **Owns:** The only place where the pipeline sequence is defined; all other modules are called from here
