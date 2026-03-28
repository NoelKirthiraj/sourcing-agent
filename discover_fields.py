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
               "X-API-Key": c.api_key, "X-User-Key": c.user_key}

    async with httpx.AsyncClient(base_url=c.base_url, headers=headers, timeout=30) as client:
        # Step 1: List all workflows
        r = await client.get("/api/Public/workflows")
        if r.status_code != 200:
            print(f"\n❌  Could not reach CFlow API (HTTP {r.status_code}). Check credentials in .env.")
            return

        workflows = r.json()
        if not isinstance(workflows, list):
            workflows = workflows.get("data", [])
        print(f"\nWorkflows found:\n")
        for wf in workflows:
            name = wf.get("workflowName") or wf.get("name") or str(wf)
            marker = "  ◀ TARGET" if c.workflow_name.lower() in name.lower() else ""
            print(f"  {name}{marker}")

        target = next((wf for wf in workflows if c.workflow_name.lower() in
                       (wf.get("workflowName") or wf.get("name") or "").lower()), None)
        if not target:
            print(f"\n❌  Workflow '{c.workflow_name}' not found. Update CFLOW_WORKFLOW_NAME in .env.")
            return

        wf_name = target.get("workflowName") or target.get("name")
        print(f"\n✅  Target workflow: '{wf_name}'\n")

        # Step 2: Get stages for the workflow
        stage_list = []
        r = await client.get(f"/api/Public/workflow/stages/{wf_name}")
        if r.status_code == 200:
            stages = r.json()
            stage_list = stages if isinstance(stages, list) else stages.get("data", [])
            print("Stages:")
            for s in stage_list:
                s_name = s.get("stageName") or s.get("stageDisplayName") or str(s)
                print(f"  {s_name}")
            print()
        else:
            print(f"⚠️  Could not retrieve stages (HTTP {r.status_code}).\n")

        # Step 3: Get fields via POST /api/Public/fields
        stage_name = c.stage_name
        if not stage_name and stage_list:
            stage_name = stage_list[0].get("stageName") or stage_list[0].get("stageDisplayName") or ""
            print(f"ℹ️  CFLOW_STAGE_NAME not set — using first stage: '{stage_name}'\n")
        r = await client.post("/api/Public/fields", json={
            "workflowName": wf_name,
            "stageName": stage_name,
        })
        if r.status_code != 200:
            print(f"⚠️  Could not retrieve fields (HTTP {r.status_code}). Check stage name in .env.")
            return

        fields_resp = r.json()
        fields = fields_resp.get("sectionFields", [])
        date_format = fields_resp.get("dateFormat", "")
        if date_format:
            print(f"Date format: {date_format}\n")

        print(f"{'#':<4} {'Display Name':<40} {'Field Name':<35} {'Type':<15} {'Required'}")
        print(f"{'─'*4} {'─'*40} {'─'*35} {'─'*15} {'─'*8}")
        for i, f in enumerate(fields, 1):
            label = f.get("displayName") or ""
            api_name = f.get("fieldName") or f.get("displayName") or ""
            ftype = f.get("fieldType") or f.get("dataType") or ""
            required = "YES" if f.get("isMandatory") else ""
            print(f"{i:<4} {label:<40} {api_name:<35} {ftype:<15} {required}")

        # Also show table fields if any
        tables = fields_resp.get("tables", [])
        if tables:
            print(f"\nTable sections:")
            for t in tables:
                print(f"  {t.get('tableName', t)}")

        print(f"\n{SEP}")
        print("Copy this into cflow_client.py → _build_payload() → values:\n")
        recipe = ["Solicitation Title","Solicitation No","GSIN Description","Inquiry Link",
                  "Closing Date","Time and Zone","Notifications","Client","Contact Name","Contact Email","Contact Phone"]
        keys = ["solicitation_title","solicitation_no","gsin_description","inquiry_link",
                "closing_date","time_and_zone","notifications","client","contact_name","contact_email","contact_phone"]
        print('            "values": {')
        for recipe_name, scraper_key in zip(recipe, keys):
            match = next((f for f in fields if recipe_name.lower() in
                          (f.get("displayName") or "").lower()), None)
            cflow_key = (match.get("displayName") or recipe_name) if match else f'{recipe_name}  # ← VERIFY'
            print(f'                "{cflow_key}": tender.get("{scraper_key}", ""),')
        print('                "Source": "CanadaBuys Auto-Agent",')
        print('            },')

if __name__ == "__main__":
    asyncio.run(discover_fields())
