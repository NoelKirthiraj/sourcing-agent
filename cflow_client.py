"""
CFlow REST API Client.
See agents/cflow.md for the field mapping table and API reference.
⚠️  Run: python run.py --discover-fields
    Then update _build_payload() with your actual CFlow field names.
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
import httpx

log = logging.getLogger(__name__)

@dataclass
class CFlowConfig:
    base_url: str
    api_key: str
    user_key: str
    username: str
    workflow_name: str
    stage_name: str = ""
    submit_immediately: bool = True

class CFlowClient:
    def __init__(self, config: CFlowConfig):
        self.config = config
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            headers={
                "Accept": "application/json",
                "X-API-Key": config.api_key,
                "X-User-Key": config.user_key,
            },
            timeout=30.0,
        )

    async def create_sourcing_request(self, tender: dict[str, Any]) -> str:
        if not self.config.stage_name:
            await self._discover_stage()
        payload = self._build_payload(tender)
        response = await self._http.post("/api/Public/submit", json=payload)
        if response.status_code not in (200, 201):
            raise RuntimeError(f"CFlow API returned {response.status_code}: {response.text}")
        data = response.json()
        return str(data.get("recordId") or data.get("record_id") or data.get("id") or data)

    async def _discover_stage(self):
        """Fetch the first stage for the workflow when CFLOW_STAGE_NAME is not set."""
        wf = self.config.workflow_name
        r = await self._http.get(f"/api/Public/workflow/stages/{wf}")
        if r.status_code != 200:
            raise RuntimeError(f"Cannot discover stages for '{wf}' (HTTP {r.status_code}): {r.text}")
        stages = r.json()
        stage_list = stages if isinstance(stages, list) else stages.get("data", [])
        if not stage_list:
            raise RuntimeError(f"No stages found for workflow '{wf}'")
        self.config.stage_name = stage_list[0].get("stageName") or stage_list[0].get("stageDisplayName")
        log.info("Auto-discovered stage: '%s'", self.config.stage_name)

    def _build_payload(self, tender: dict[str, Any]) -> dict[str, Any]:
        # Field names verified against CFlow record #114 (PDF export).
        # Run: python run.py --discover-fields  to re-verify.
        title = tender.get("solicitation_title", "")
        sol_no = tender.get("solicitation_no", "")
        closing_raw = tender.get("closing_date", "")
        closing_date = self._format_date(closing_raw)
        inquiry_link = tender.get("inquiry_link", "")
        notification_link = self._notification_url(inquiry_link)

        return {
            "workflowName": self.config.workflow_name,
            "stageName": self.config.stage_name,
            "isDraft": not self.config.submit_immediately,
            "isWorkflow": True,
            "values": {
                "Solicitation Title":      title,
                "Solicitation No":         sol_no,
                "Solicitation Title / No": f"{title} - {sol_no}" if sol_no else title,
                "Inquiry Link":            inquiry_link,
                "Closing Date":            closing_date,
                "Time and Zone":           tender.get("time_and_zone", ""),
                "Submitted Date":          datetime.now().strftime("%m/%d/%Y"),
                "Notification Link":       notification_link,
                "Client 1":               tender.get("client", ""),
                "Contact Name":            tender.get("contact_name", ""),
                "Contact E-Mail":          tender.get("contact_email", ""),
                "Contact Phone":           tender.get("contact_phone", ""),
                "GSN":                     tender.get("gsin_description", ""),
                "Number of Amendment(s)":  tender.get("notifications", ""),
                "Inquiry (CONTRACT or SAP)": tender.get("bid_platform", ""),
            },
        }

    @staticmethod
    def _format_date(raw: str) -> str:
        """Convert portal date formats to CFlow's MM/DD/YYYY."""
        if not raw:
            return ""
        cleaned = raw.strip()
        # Portal returns e.g. "2026/04/14 14:00 EDT" — take date portion only for single-token formats.
        date_token = cleaned.split()[0]
        for fmt, value in [
            ("%Y/%m/%d", date_token),
            ("%Y-%m-%d", date_token),
            ("%B %d, %Y", cleaned),      # "April 14, 2026" — needs full string
        ]:
            try:
                return datetime.strptime(value, fmt).strftime("%m/%d/%Y")
            except (ValueError, IndexError):
                continue
        # Already in MM/DD/YYYY or unrecognized — pass through
        return cleaned

    @staticmethod
    def _notification_url(inquiry_link: str) -> str:
        """Derive the notifications URL from the tender inquiry link."""
        if not inquiry_link:
            return ""
        # e.g. .../tender-notice/ws5569057012-doc5575896251 → .../tender-notice/{id}/notifications
        m = re.search(r"/tender-notice/([^/]+)", inquiry_link)
        if m:
            tender_id = m.group(1)
            return f"https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/{tender_id}/notifications"
        return ""

    async def attach_solicitation(self, record_id: str, file_path: str) -> bool:
        """Upload a solicitation file to the 'UpLoad Solicitation' field on a record."""
        if not self.config.stage_name:
            await self._discover_stage()
        filename = file_path.rsplit("/", 1)[-1]
        with open(file_path, "rb") as f:
            response = await self._http.post(
                "/api/Public/filefield/attachfile",
                data={
                    "workflowName": self.config.workflow_name,
                    "stageName": self.config.stage_name,
                    "recordId": record_id,
                    "fieldName": "UpLoad Solicitation",
                },
                files={"file": (filename, f)},
            )
        if response.status_code not in (200, 201):
            log.error("File upload failed for record %s: %s %s", record_id, response.status_code, response.text)
            return False
        log.info("  Uploaded %s to record %s", filename, record_id)
        return True

    async def aclose(self):
        await self._http.aclose()
