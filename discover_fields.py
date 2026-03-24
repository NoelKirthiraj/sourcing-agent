"""
CFlow Field Discovery Tool — run once before first live submission.
Queries CFlow API and outputs the exact field names for your sourcing workflow,
plus a ready-to-paste _build_payload() code block.

Usage:  python run.py --discover-fields
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
SEP = "─" * 70

async def discover_fields():
    print(f"\n🔍  CFlow Field Discovery\n{SEP}")
    config = Config.load()
    c = config.cflow
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "api-key": c.api_key, "user-key": c.user_key, "username": c.username}

    async with httpx.AsyncClient(base_url=c.base_url, headers=headers, timeout=30) as client:
        workflows_data = None
        for ep in ["/api/v1/workflows", "/api/v1/processes", "/cflownew/api/v1/workflows"]:
            try:
                r = await client.get(ep)
                if r.status_code == 200:
                    workflows_data = r.json()
                    break
            except Exception:
                pass

        if not workflows_data:
            print("\n❌  Could not reach CFlow API. Check credentials in .env.")
            return

        workflows = workflows_data if isinstance(workflows_data, list) else workflows_data.get("data", [])
        print(f"\nWorkflows found:\n")
        for wf in workflows:
            name = wf.get("name") or wf.get("workflow_name") or wf.get("title") or str(wf)
            marker = "  ◀ TARGET" if c.workflow_name.lower() in name.lower() else ""
            print(f"  {name}{marker}")

        target = next((wf for wf in workflows if c.workflow_name.lower() in
                       (wf.get("name") or wf.get("workflow_name") or wf.get("title") or "").lower()), None)
        if not target:
            print(f"\n❌  Workflow '{c.workflow_name}' not found. Update CFLOW_WORKFLOW_NAME in .env.")
            return

        wf_id = target.get("id") or target.get("workflow_id")
        wf_name = target.get("name") or target.get("workflow_name") or target.get("title")
        print(f"\n✅  Target workflow: '{wf_name}' (id={wf_id})\n")

        fields_data = None
        for ep in [f"/api/v1/workflows/{wf_id}/fields", f"/api/v1/processes/{wf_id}/fields"]:
            try:
                r = await client.get(ep)
                if r.status_code == 200:
                    fields_data = r.json()
                    break
            except Exception:
                pass

        if not fields_data:
            print("⚠️  Could not retrieve fields. Check field names manually in the CFlow form builder.")
            return

        fields = fields_data if isinstance(fields_data, list) else fields_data.get("fields", fields_data.get("data", []))
        print(f"{'#':<4} {'Label':<40} {'API Name':<35} {'Type'}")
        print(f"{'─'*4} {'─'*40} {'─'*35} {'─'*15}")
        for i, f in enumerate(fields, 1):
            label = f.get("label") or f.get("field_label") or f.get("name") or ""
            api_name = f.get("api_name") or f.get("key") or f.get("field_name") or f.get("id") or ""
            ftype = f.get("type") or f.get("field_type") or ""
            print(f"{i:<4} {label:<40} {api_name:<35} {ftype}")

        print(f"\n{SEP}")
        print("Copy this into cflow_client.py → _build_payload() → form_fields:\n")
        recipe = ["Solicitation Title","Solicitation No","GSIN Description","Inquiry Link",
                  "Closing Date","Time and Zone","Notifications","Client","Contact Name","Contact Email","Contact Phone"]
        keys = ["solicitation_title","solicitation_no","gsin_description","inquiry_link",
                "closing_date","time_and_zone","notifications","client","contact_name","contact_email","contact_phone"]
        print('            "form_fields": {')
        for recipe_name, scraper_key in zip(recipe, keys):
            match = next((f for f in fields if recipe_name.lower() in
                          (f.get("label") or f.get("field_label") or f.get("name") or "").lower()), None)
            cflow_key = (match.get("api_name") or match.get("key") or match.get("field_name") or f'"{recipe_name}"') if match else f'"{recipe_name}"  # ← VERIFY'
            print(f'                "{cflow_key}": tender.get("{scraper_key}", ""),')
        print('                "Source": "CanadaBuys Auto-Agent",')
        print('            },')

if __name__ == "__main__":
    asyncio.run(discover_fields())
