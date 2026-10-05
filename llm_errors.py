"""
Shared classification of Anthropic API failures.

On 2026-09-07 an exhausted API balance surfaced as "SAP: no Download Content
button found by vision" — a billing problem dressed up as a page problem that
cost about an hour to diagnose. The fix was to name the real cause; this module
holds that logic in one place so each new caller doesn't reimplement it.

`po_extractor` keeps its own variant because it raises a typed
`PoExtractionError` with an HTTP status rather than returning a reason string.
Consolidating that one is worthwhile but not urgent.
"""
from __future__ import annotations

from typing import Optional


def api_unavailable_reason(exc: Exception) -> Optional[str]:
    """Operator-facing reason if `exc` means the API could not run at all.

    Returns None for ordinary failures — timeouts, parse errors, 5xx — which
    genuinely mean "we tried and got nothing" and should keep whatever
    best-effort behaviour the caller already has.
    """
    msg = str(exc).lower()
    if "credit balance" in msg or "insufficient" in msg or "low balance" in msg:
        return ("Anthropic API credit balance is exhausted. Top up at "
                "https://console.anthropic.com/settings/billing and retry.")
    if "quota" in msg or "usage limit" in msg or "spend limit" in msg:
        return ("Anthropic API usage/quota limit reached. Check plan limits at "
                "https://console.anthropic.com/settings/limits.")
    if "authentication" in msg or "invalid x-api-key" in msg or "api key" in msg:
        return ("Anthropic API key is missing or invalid — check the "
                "ANTHROPIC_API_KEY secret.")
    return None
