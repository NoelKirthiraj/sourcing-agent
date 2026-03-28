# CFlow Agent

## Responsibility

Maps scraped tender fields to CFlow form field names and submits workflow requests via the CFlow REST API using HTTPX.

## Key Files

```
cflow_client.py                    # CFlowConfig + CFlowClient
discover_fields.py                 # One-time tool — run before first live submission
tests/unit/
  test_cflow_client.py             # Payload mapping unit tests
tests/integration/
  test_cflow_api.py                # Mocked CFlow REST API tests (respx)
```

## Commands

```bash
# Discover exact field names from the live CFlow workflow (run this first)
python run.py --discover-fields

# Test payload mapping without hitting CFlow
pytest tests/unit/test_cflow_client.py -v

# Test CFlow API integration (mocked — no live CFlow needed)
pytest tests/integration/test_cflow_api.py -v

# Submit draft records for manual CFlow UI review
# (set CFLOW_SUBMIT_NOW=false in .env first)
python run.py --limit 3
```

## Verification

After changing `_build_payload()` or any CFlow-related code:

1. Run `pytest tests/unit/test_cflow_client.py` — all mapping tests must pass
2. Run `pytest tests/integration/test_cflow_api.py` — mocked API tests must pass
3. Set `CFLOW_SUBMIT_NOW=false`, run `python run.py --limit 2`, verify draft records in CFlow UI
4. Confirm `Source` field reads `"CanadaBuys Auto-Agent"` in the CFlow record
5. Confirm `Inquiry Link` is a clickable URL in CFlow

## Patterns

### Correct — use `.get()` with empty string default in values

```python
"values": {
    "Solicitation Title": tender.get("solicitation_title", ""),
    "Contact Email":      tender.get("contact_email", ""),
}
```

### Wrong — direct dict access risks KeyError; None values cause 422

```python
"values": {
    "Solicitation Title": tender["solicitation_title"],   # KeyError if missing
    "Contact Email":      tender.get("contact_email"),    # None → 422 from CFlow
}
```

### Correct — check both 200 and 201 as success

```python
if response.status_code not in (200, 201):
    raise RuntimeError(f"CFlow API returned {response.status_code}: {response.text}")
```

### Correct — extract recordId from response

```python
record_id = str(data.get("recordId") or data.get("record_id") or data.get("id") or data)
```

## Common Mistakes

- **Don't** use the CFlow UI display label as the API field key without verifying.
  **Do** run `discover_fields.py` — the API name and the UI label are often different (e.g., UI shows "Solicitation Title", API expects `sol_title` or similar).

- **Don't** forget the `Source` field in `_build_payload()`.
  **Do** always include `"Source": "CanadaBuys Auto-Agent"` — it lets the sourcing team filter agent-created records from manually-created ones in CFlow.

- **Don't** change the `submit_immediately` logic in the hot path.
  **Do** control it exclusively via `CFLOW_SUBMIT_NOW` env var — never hardcode `True` or `False` in source.

- **Don't** retry failed CFlow POSTs within the same run.
  **Do** let failures propagate up to the orchestrator as `RuntimeError` — the orchestrator logs them and the dedup state ensures they retry next run automatically.

- **Don't** log the full response body at INFO level on every successful POST.
  **Do** log only the `request_id` at INFO; log full response at DEBUG only.

## CFlow API Reference

```
Regional base URLs:
  US:  https://pubapi-us.cflowapps.com/cflowpublicapi
  AP:  https://pubapi-ap.cflowapps.com/cflowpublicapi
  EU:  https://pubapi-eu.cflowapps.com/cflowpublicapi
  ME:  https://pubapi-me.cflowapps.com/cflowpublicapi

Submit endpoint:   POST /api/Public/submit          (isDraft: true/false)
Workflows list:    GET  /api/Public/workflows
Stages for WF:     GET  /api/Public/workflow/stages/{workflowName}
Fields for WF:     POST /api/Public/fields           (body: workflowName + stageName)
Record details:    POST /api/Public/recorddetails
Search records:    POST /api/Public/searchrecord

Auth headers (all required on every request):
  X-API-Key:   [from Admin → Security Settings → API Settings]
  X-User-Key:  [from Profile → API Key]

Optional headers:
  X-Impersonate-User:  [username to act on behalf of]

Success responses:  200 or 201
Field error:        422  (check field names with discover_fields.py)
Auth error:         401  (rotate keys)
Forbidden:          403  (insufficient permissions)
Not found:          404  (wrong base_url or workflow_name)

Submit payload structure:
  {
    "workflowName": "...",
    "stageName": "...",
    "isDraft": true/false,
    "isWorkflow": true,
    "values": { "FieldName": "value", ... },
    "tableValues": [{ "TableName": "...", "Values": [...] }]
  }

Submit response:
  { "recordId": 123, "status": "..." }
```

## Field Mapping Table (Update After Running discover_fields.py)

| Data Miner Recipe Field | Scraper Key | CFlow API Field Name |
|-------------------------|-------------|----------------------|
| Solicitation Title | `solicitation_title` | *(verify with discover_fields)* |
| Solicitation No | `solicitation_no` | *(verify with discover_fields)* |
| GSIN Description | `gsin_description` | *(verify with discover_fields)* |
| Inquiry Link | `inquiry_link` | *(verify with discover_fields)* |
| Closing Date | `closing_date` | *(verify with discover_fields)* |
| Time and Zone | `time_and_zone` | *(verify with discover_fields)* |
| Notifications | `notifications` | *(verify with discover_fields)* |
| Client | `client` | *(verify with discover_fields)* |
| Contact Name | `contact_name` | *(verify with discover_fields)* |
| Contact Email | `contact_email` | *(verify with discover_fields)* |
| Contact Phone | `contact_phone` | *(verify with discover_fields)* |
| *(agent-added)* | — | `Source` = `"CanadaBuys Auto-Agent"` |

> Fill in the "CFlow API Field Name" column after running `python run.py --discover-fields`. This is the only setup step that requires a live CFlow account.

## Dependencies

- **Depends on:** `config.py` (CFlowConfig)
- **Depended on by:** `agent.py` (calls `create_sourcing_request()`)
- **External:** CFlow REST API at `CFLOW_BASE_URL`
