"""Unit tests for scraper.py utility functions (no browser needed)."""
import pytest
from scraper import _clean, _clean_html, _absolute, _capture, DAILY_URL, WEEKLY_URL, BASE_URL


class TestClean:
    def test_strips_leading_trailing_whitespace(self):
        assert _clean("  hello  ") == "hello"

    def test_collapses_internal_whitespace(self):
        assert _clean("hello   world") == "hello world"

    def test_collapses_tabs_and_newlines(self):
        assert _clean("hello\t\n  world") == "hello world"

    def test_none_returns_empty(self):
        assert _clean(None) == ""

    def test_empty_string(self):
        assert _clean("") == ""

    def test_single_word(self):
        assert _clean("word") == "word"


class TestCleanHtml:
    def test_removes_simple_tags(self):
        assert "hello" in _clean_html("<b>hello</b>")

    def test_removes_nested_tags(self):
        result = _clean_html("<div><span>text</span></div>")
        assert "text" in result
        assert "<" not in result

    def test_decodes_amp(self):
        assert "&" in _clean_html("A&amp;B")

    def test_decodes_apostrophe(self):
        assert "'" in _clean_html("it&#39;s")

    def test_decodes_quote(self):
        assert '"' in _clean_html("&quot;quoted&quot;")

    def test_none_returns_empty(self):
        assert _clean_html(None) == ""

    def test_empty_returns_empty(self):
        assert _clean_html("") == ""


class TestAbsolute:
    def test_relative_path_becomes_absolute(self):
        result = _absolute("/en/tender-opportunities/tender-notice/123")
        assert result == "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/123"

    def test_already_absolute_url(self):
        url = "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/123"
        assert _absolute(url) == url

    def test_none_returns_empty(self):
        assert _absolute(None) == ""

    def test_empty_returns_empty(self):
        assert _absolute("") == ""


class TestCapture:
    def test_extracts_match(self):
        text = "Solicitation number\nPW-EZZ-123-456"
        result = _capture(text, r"Solicitation number\s+([^\n]+)")
        assert result == "PW-EZZ-123-456"

    def test_no_match_returns_empty(self):
        assert _capture("no match here", r"missing\s+(\d+)") == ""

    def test_cleans_captured_value(self):
        text = "Field\n  value with   spaces  "
        result = _capture(text, r"Field\s+(.+)")
        assert result == "value with spaces"

    def test_with_custom_flags(self):
        import re
        text = "Email test@EXAMPLE.COM"
        result = _capture(text, r"Email\s+([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", flags=re.IGNORECASE)
        assert result == "test@EXAMPLE.COM"


class TestURLConstants:
    def test_daily_url_has_record_per_page_200(self):
        assert "record_per_page=200" in DAILY_URL

    def test_weekly_url_has_record_per_page_200(self):
        assert "record_per_page=200" in WEEKLY_URL

    def test_daily_url_has_no_category_filter(self):
        assert "category" not in DAILY_URL

    def test_weekly_url_has_goods_category_153(self):
        assert "category%5B153%5D=153" in WEEKLY_URL

    def test_daily_url_has_last_24h_filter(self):
        # pub[1]=1 is the "Last 24 hours" filter
        assert "pub%5B1%5D=1" in DAILY_URL

    def test_weekly_url_has_last_7_days_filter(self):
        # pub[2]=2 is the "Last 7 days" filter
        assert "pub%5B2%5D=2" in WEEKLY_URL

    def test_both_urls_filter_open_status(self):
        # status[87]=87 is the "Open" status filter
        assert "status%5B87%5D=87" in DAILY_URL
        assert "status%5B87%5D=87" in WEEKLY_URL
