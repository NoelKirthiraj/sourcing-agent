"""Unit tests for cflow_client.py — payload mapping, date formatting, URL derivation."""
import pytest
from cflow_client import CFlowClient, CFlowConfig


@pytest.fixture
def client():
    config = CFlowConfig(
        base_url="https://pubapi-us.cflowapps.com/cflowpublicapi",
        api_key="test-key",
        user_key="test-user-key",
        username="test@example.com",
        workflow_name="Test Workflow",
        stage_name="Stage 1",
    )
    return CFlowClient(config)


@pytest.fixture
def sample_tender():
    return {
        "solicitation_title": "CCGS Gordon Reid - Search and Rescue Crane",
        "solicitation_no": "WS5569057012",
        "gsin_description": "Marine craft systems",
        "inquiry_link": "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/ws5569057012-doc5575896251",
        "closing_date": "2026/04/14",
        "time_and_zone": "14:00 EDT",
        "notifications": "2026/03/20",
        "client": "Canadian Coast Guard",
        "contact_name": "Patrick Wass",
        "contact_email": "patrick.wass@tpsgc-pwgsc.gc.ca",
        "contact_phone": "613-555-0100",
        "bid_platform": "CanadaBuys",
    }


class TestFormatDate:
    def test_yyyy_slash_mm_dd(self):
        assert CFlowClient._format_date("2026/04/14") == "04/14/2026"

    def test_yyyy_dash_mm_dd(self):
        assert CFlowClient._format_date("2026-04-14") == "04/14/2026"

    def test_month_name_format(self):
        assert CFlowClient._format_date("April 14, 2026") == "04/14/2026"

    def test_date_with_trailing_time(self):
        assert CFlowClient._format_date("2026/04/14 14:00 EDT") == "04/14/2026"

    def test_empty_string(self):
        assert CFlowClient._format_date("") == ""

    def test_already_mm_dd_yyyy(self):
        # Unrecognized format passes through
        assert CFlowClient._format_date("04/14/2026") == "04/14/2026"


class TestNotificationUrl:
    def test_derives_from_tender_notice_url(self):
        url = "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/ws5569057012-doc5575896251"
        result = CFlowClient._notification_url(url)
        assert result == "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/ws5569057012-doc5575896251/notifications"

    def test_empty_url(self):
        assert CFlowClient._notification_url("") == ""

    def test_non_tender_notice_url(self):
        assert CFlowClient._notification_url("https://example.com/something") == ""


class TestBuildPayload:
    def test_payload_structure(self, client, sample_tender):
        payload = client._build_payload(sample_tender)
        assert payload["workflowName"] == "Test Workflow"
        assert payload["stageName"] == "Stage 1"
        assert payload["isDraft"] is False  # submit_immediately defaults True
        assert payload["isWorkflow"] is True
        assert "values" in payload

    def test_combined_title_no(self, client, sample_tender):
        payload = client._build_payload(sample_tender)
        assert payload["values"]["Solicitation Title / No"] == "CCGS Gordon Reid - Search and Rescue Crane - WS5569057012"

    def test_combined_title_no_without_sol_no(self, client, sample_tender):
        sample_tender["solicitation_no"] = ""
        payload = client._build_payload(sample_tender)
        assert payload["values"]["Solicitation Title / No"] == "CCGS Gordon Reid - Search and Rescue Crane"

    def test_closing_date_formatted(self, client, sample_tender):
        payload = client._build_payload(sample_tender)
        assert payload["values"]["Closing Date"] == "04/14/2026"

    def test_notification_link_derived(self, client, sample_tender):
        payload = client._build_payload(sample_tender)
        assert "/notifications" in payload["values"]["Notification Link"]

    def test_bid_platform_set(self, client, sample_tender):
        payload = client._build_payload(sample_tender)
        assert payload["values"]["Inquiry (CONTRACT or SAP)"] == "CanadaBuys"

    def test_sap_platform(self, client, sample_tender):
        sample_tender["bid_platform"] = "SAP"
        payload = client._build_payload(sample_tender)
        assert payload["values"]["Inquiry (CONTRACT or SAP)"] == "SAP"

    def test_missing_fields_default_to_empty(self, client):
        payload = client._build_payload({})
        assert payload["values"]["Solicitation Title"] == ""
        assert payload["values"]["Contact E-Mail"] == ""
        assert payload["values"]["Client 1"] == ""

    def test_is_draft_when_not_submit_immediately(self):
        config = CFlowConfig(
            base_url="https://example.com",
            api_key="k",
            user_key="u",
            username="u@e.com",
            workflow_name="W",
            stage_name="S",
            submit_immediately=False,
        )
        c = CFlowClient(config)
        payload = c._build_payload({})
        assert payload["isDraft"] is True
