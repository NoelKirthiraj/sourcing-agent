"""
AgentState — tracks processed solicitation numbers across runs.
Prevents duplicate CFlow entries.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

class AgentState:
    def __init__(self, path: Path = Path("processed_solicitations.json")):
        self._path = path
        self._data: dict[str, Any] = self._load()
        self._links_set: set[str] = {
            v.get("link", "") for v in self._data.values() if v.get("link")
        }

    def already_processed(self, solicitation_no: str) -> bool:
        return solicitation_no in self._data

    def already_processed_by_link(self, link: str) -> bool:
        """Fast check by inquiry_link — used to skip detail page fetch."""
        return link in self._links_set

    def mark_processed(self, solicitation_no: str, *, request_id: str = "", title: str = "", link: str = ""):
        self._data[solicitation_no] = {
            "cflow_request_id": request_id,
            "title": title,
            "link": link,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        if link:
            self._links_set.add(link)

    def save(self):
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        log.info("State saved: %d solicitations tracked (%s)", len(self._data), self._path)

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                log.info("Loaded state: %d previously processed solicitations", len(data))
                return data
            except Exception as exc:
                log.warning("Could not read state file %s: %s — starting fresh", self._path, exc)
        return {}
