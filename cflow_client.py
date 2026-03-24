"""
CFlow REST API Client.
See agents/cflow.md for the field mapping table and API reference.
⚠️  Run: python run.py --discover-fields
    Then update _build_payload() with your actual CFlow field names.
"""
import logging
from dataclasses import dataclass
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
    submit_immediately: bool = True

class CFlowClient:
    def __init__(self, config: CFlowConfig):
        self.config = config
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "api-key": config.api_key,
                "user-key": config.user_key,
                "username": config.username,
            },
            timeout=30.0,
        )

    async def create_sourcing_request(self, tender: dict[str, Any]) -> str:
        payload = self._build_payload(tender)
        endpoint = "/api/v1/requests" if self.config.submit_immediately else "/api/v1/requests/draft"
        response = await self._http.post(endpoint, json=payload)
        if response.status_code not in (200, 201):
            raise RuntimeError(f"CFlow API returned {response.status_code}: {response.text}")
        data = response.json()
        return str(data.get("request_id") or data.get("id") or data.get("record_id") or data)

    def _build_payload(self, tender: dict[str, Any]) -> dict[str, Any]:
        # ⚠️  Update keys below to match your CFlow workflow field names.
        # Run: python run.py --discover-fields  to get the exact names.
        return {
            "workflow_name": self.config.workflow_name,
            "form_fields": {
                "Solicitation Title":  tender.get("solicitation_title", ""),
                "Solicitation No":     tender.get("solicitation_no", ""),
                "GSIN Description":    tender.get("gsin_description", ""),
                "Inquiry Link":        tender.get("inquiry_link", ""),
                "Closing Date":        tender.get("closing_date", ""),
                "Time and Zone":       tender.get("time_and_zone", ""),
                "Notifications":       tender.get("notifications", ""),
                "Client":              tender.get("client", ""),
                "Contact Name":        tender.get("contact_name", ""),
                "Contact Email":       tender.get("contact_email", ""),
                "Contact Phone":       tender.get("contact_phone", ""),
                "Source":              "CanadaBuys Auto-Agent",
            },
        }

    async def aclose(self):
        await self._http.aclose()
