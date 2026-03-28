"""
Configuration — loads all settings from environment variables.
Fails fast with a clear error if any required variable is missing.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv
from scraper import ScraperConfig
from cflow_client import CFlowConfig

load_dotenv()

def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set.\n"
            f"Add it to your .env file — see docs/SETUP.md."
        )
    return val

def _bool_env(key: str, default: bool = True) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes")

@dataclass
class Config:
    cflow: CFlowConfig
    scraper: ScraperConfig

    @classmethod
    def load(cls) -> "Config":
        cflow = CFlowConfig(
            base_url=_require("CFLOW_BASE_URL"),
            api_key=_require("CFLOW_API_KEY"),
            user_key=_require("CFLOW_USER_KEY"),
            username=_require("CFLOW_USERNAME"),
            workflow_name=_require("CFLOW_WORKFLOW_NAME"),
            stage_name=os.getenv("CFLOW_STAGE_NAME", "").strip(),
            submit_immediately=_bool_env("CFLOW_SUBMIT_NOW", default=True),
        )
        default_url = (
            "https://canadabuys.canada.ca/en/tender-opportunities"
            "?search_filter=&pub%5B1%5D=1&status%5B87%5D=87"
            "&Apply_filters=Apply+filters&record_per_page=200&current_tab=t&words="
        )
        scraper = ScraperConfig(
            search_url=os.getenv("SCRAPER_URL", default_url),
            headless=_bool_env("SCRAPER_HEADLESS", default=True),
        )
        return cls(cflow=cflow, scraper=scraper)
