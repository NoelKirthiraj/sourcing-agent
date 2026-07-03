"""
PO extractor — LLM-powered extraction of contract + supplier-quote fields.

Two entry points:
  * extract_contract(pdf_bytes, filename) → dict
  * extract_quote(pdf_bytes, filename) → dict

Both run Claude vision against the raw PDF and return a strict-schema dict.
Pre-flight validation rejects oversized, non-PDF, encrypted, or scan-only files
before they hit the API, so the user gets a clear error instead of a silent
empty draft.

Failures are surfaced as `PoExtractionError` with a `user_message` that the
HTTP layer can pass straight to the frontend.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)

# Upload caps. Tuned for Anthropic's per-doc limit (32 MB) and to keep DB rows small.
MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_MAX_TOKENS = 8192
ANTHROPIC_TIMEOUT_SECONDS = 90.0


class PoExtractionError(Exception):
    """Raised when extraction cannot proceed. `user_message` is safe to surface."""

    def __init__(self, user_message: str, *, status: int = 400):
        super().__init__(user_message)
        self.user_message = user_message
        self.status = status


# ── Pre-flight ────────────────────────────────────────────────────────────────

def validate_pdf(data: bytes, *, role: str) -> None:
    """Reject obviously-bad uploads before we call the API.

    role is "contract" or "quote" — used in user-facing messages.
    """
    if not data:
        raise PoExtractionError(f"{role.title()} upload was empty.")
    if len(data) > MAX_PDF_BYTES:
        mb = len(data) / (1024 * 1024)
        raise PoExtractionError(
            f"{role.title()} PDF is {mb:.1f} MB — must be 10 MB or smaller.",
            status=413,
        )
    # PDF magic bytes — accept a small whitespace/garbage prefix because some
    # exports prepend bytes before the header.
    head = data[:1024]
    if b"%PDF-" not in head:
        raise PoExtractionError(f"{role.title()} file does not look like a PDF.")

    # Encrypted PDFs render as gibberish through Claude — detect early.
    if b"/Encrypt" in data[:65536]:
        raise PoExtractionError(
            f"{role.title()} PDF is encrypted. Please decrypt it and try again.",
        )


def looks_like_scan(data: bytes) -> bool:
    """Cheap heuristic: a text-PDF will contain stream text operators within
    the first 256 KB. Image-only scans almost never do.

    Used to *warn* the user; does not block extraction (Claude vision can
    sometimes OCR a scan, and we'd rather try than refuse).
    """
    head = data[:262144]
    return not (b"/Font" in head or b"BT\n" in head or b"Tj" in head or b"TJ" in head)


# ── Role detection (auto-swap protection) ─────────────────────────────────────

_CONTRACT_KEYWORDS = (
    b"National Defence", b"Defence nationale", b"Contracting Authority",
    b"DND CONTRACTING", b"Public Works", b"Standard Acquisition",
    b"Contract No.", b"DAP", b"Materiel Acquisition",
)
_QUOTE_KEYWORDS = (
    b"QUOTE", b"Quotation", b"Sold To:", b"Quote #", b"NET SALES",
    b"AMOUNT DUE", b"CAGE CODE", b"Tronair",
)


def detect_role(data: bytes) -> str:
    """Return 'contract' | 'quote' | 'unknown' from PDF byte heuristics."""
    head = data[:524288]
    contract_hits = sum(1 for k in _CONTRACT_KEYWORDS if k in head)
    quote_hits = sum(1 for k in _QUOTE_KEYWORDS if k in head)
    if contract_hits > quote_hits and contract_hits >= 2:
        return "contract"
    if quote_hits > contract_hits and quote_hits >= 2:
        return "quote"
    return "unknown"


# ── Prompts ───────────────────────────────────────────────────────────────────

CONTRACT_PROMPT = """You are reading a Government of Canada / DND awarded contract document.

Extract the structured data and return ONLY valid JSON, no markdown, no commentary.

Schema:
{
  "contract_no": "the contract / invitation number, e.g. W8485-258676.002",
  "contract_date": "ISO date of contract award, e.g. 2025-09-16, or empty string if missing",
  "client_reference": "any internal/customer reference number shown on the contract — e.g. 'RAD-7813', 'Vendor Ref: RAD-7813', 'Your File No: 12345', 'Supplier Reference: ...'. Returns the raw reference string as-is. Empty string if not present.",
  "title": "short contract title, e.g. '20 ton Jack Parts'",
  "currency": "USD or CAD — match what the contract states",
  "delivery_date": "the final delivery date as it appears in the contract (e.g. '31 March 2026')",
  "delivery_address": {
    "name": "destination facility name",
    "lines": ["street", "city, province/state", "postal/zip", "country"]
  },
  "incoterms": "the Incoterms 2020 code exactly as stated in the contract (FCA / FOB / EXW / DAP / DDP / CIF / etc.) with the named place. Empty string if the contract does not state one. Do not infer.",
  "payment_terms": "the payment terms exactly as stated in the contract. Empty string if not stated. Do not infer.",
  "contracting_authority": {
    "name": "", "title": "", "email": "", "phone": ""
  },
  "technical_authority": {
    "name": "", "title": "", "email": "", "phone": ""
  },
  "contractor_rep": {
    "name": "the contractor's representative for general enquiries",
    "email": "", "phone": ""
  },
  "items": [
    {
      "line": 1,
      "nsn": "the NSN if shown",
      "part_no": "manufacturer part number",
      "ncage": "NCAGE code if present",
      "description": "short description, e.g. TEST FIXTURE KIT",
      "qty": 2,
      "unit": "EA",
      "unit_price": 24959.55,
      "extended": 49919.10,
      "delivery_lead_time": "e.g. '20 weeks ARO'"
    }
  ],
  "flow_down_clauses": [
    "concise business-readable statements (max ~12 words each) covering only the contract terms that must flow to the supplier"
  ]
}

Rules:
- Use only data explicitly present in the document. If a field is missing, return an empty string or empty list.
- Numeric fields (qty, unit_price, extended) MUST be numbers, not strings. Strip currency symbols.
- For the items array, include only line items that have explicit unit pricing on the contract.
- If multiple items exist, return them all.
- Do not invent values. If unsure, leave blank.
- NEVER use a schema example value as the output. Examples are only for shape; if the document does not contain the field, the field MUST be empty.

Flow-down clause rules — IMPORTANT:
- Return AT MOST 8 bullets. Pick only what materially affects supplier delivery / quality / shipping.
- Each bullet MUST be a complete short sentence a supplier can read and act on. e.g.:
  - "Delivery required on or before 31 March 2026"
  - "Delivery FCA Tronair, Swanton OH USA"
  - "Packaging per D-LM-008-036/SF-000"
  - "ISO 9001:2015 quality requirements"
  - "Do not ship prior to DND shipping instructions"
  - "Commercial invoice and customs documents required"
- DO NOT emit raw clause IDs alone (e.g. "B7500C (2006-06-16) Excess Goods.", "C2000C Taxes - Foreign-based Contractor"). Those belong in the contract, not on a supplier PO.
- DO NOT emit administrative-only terms (insurance carriers, applicable-law clauses, generic payment-method codes) — those don't affect what the supplier ships.
- If the contract has compliance specs the supplier must follow (D-LM-008-036/SF-000, D2000C, D2001C, D2025C), reference them by name in a single bullet — the renderer will expand them.
"""


QUOTE_PROMPT = """You are reading a supplier's QUOTE document.

Extract the structured data and return ONLY valid JSON, no markdown, no commentary.

Schema:
{
  "quote_no": "the quote / proposal / reference number printed on the document. Empty string if the document does not have one. Do not invent.",
  "quote_date": "ISO date if shown, else empty string",
  "expires_on": "ISO expiry date if shown",
  "supplier": {
    "name": "supplier company name, e.g. Tronair",
    "lines": ["street", "city, state", "postal/zip", "country"],
    "cage_code": "",
    "duns": ""
  },
  "sold_to": {
    "name": "", "contact": "", "lines": []
  },
  "sales_rep": "name of the sales rep or contact",
  "sales_email": "",
  "currency": "USD or CAD",
  "payment_terms": "the payment terms exactly as stated on the quote. Empty string if not stated. Do not infer or use the example above.",
  "incoterms": "the Incoterms code exactly as stated on the quote, with the named place. Empty string if the quote does not state one. Do not infer or use the example above.",
  "items": [
    {
      "line": 1,
      "part_no": "supplier item / part number",
      "description": "short description",
      "qty": 2,
      "unit": "EA",
      "unit_price": 23771.00,
      "extended": 47542.00,
      "lead_time": "e.g. '20 to 22 weeks'"
    }
  ],
  "freight": 0,
  "misc_charges": 0,
  "taxes": 0,
  "net_total": 120794.00
}

Rules:
- Numeric fields MUST be numbers, not strings. Strip currency symbols and commas.
- Include ALL line items from the quote — even if the customer only ended up ordering some of them. The reconciler will match later.
- If a field is missing, return an empty string, empty object, or empty list.
- Do not invent values.
- NEVER use a schema example value as the output. Examples are only for shape; if the document does not contain the field, the field MUST be empty.
"""


# ── LLM call ──────────────────────────────────────────────────────────────────

def _strip_code_fence(text: str) -> str:
    """Remove markdown ```json ... ``` wrapping if the model added it."""
    s = text.strip()
    if s.startswith("```"):
        # Drop first line ("```json" or "```")
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _coerce_number(value: Any) -> float:
    """Best-effort numeric coercion. Returns 0.0 on failure."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.\-]", "", value)
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0
    return 0.0


def _coerce_int(value: Any) -> int:
    return int(_coerce_number(value))


def _call_claude(pdf_bytes: bytes, prompt: str) -> dict[str, Any]:
    """Send a PDF + prompt to Claude vision and parse JSON response.

    Raises PoExtractionError on terminal failures, including auth, oversized
    payloads, or invalid JSON after retries.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise PoExtractionError(
            "Extractor is misconfigured (no API key). Contact admin.",
            status=503,
        )

    try:
        import anthropic
    except ImportError as exc:
        raise PoExtractionError(
            "Anthropic SDK not installed. Run `pip install anthropic`.",
            status=503,
        ) from exc

    client = anthropic.Anthropic(api_key=api_key, timeout=ANTHROPIC_TIMEOUT_SECONDS)
    pdf_data = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    content = [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": pdf_data,
            },
        },
        {"type": "text", "text": prompt},
    ]

    last_err: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            message = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                messages=[{"role": "user", "content": content}],
            )
            response_text = message.content[0].text
            try:
                return json.loads(_strip_code_fence(response_text))
            except json.JSONDecodeError as exc:
                log.warning("Claude returned non-JSON on attempt %d: %s", attempt, exc)
                last_err = exc
                # Fall through to retry once with a stricter nudge.
                content = [
                    *content,
                    {"type": "text", "text": (
                        "Your previous reply was not valid JSON. "
                        "Reply with ONLY a single JSON object that matches the schema. "
                        "No markdown, no prose."
                    )},
                ]
                continue
        except anthropic.APITimeoutError as exc:
            last_err = exc
            log.warning("Claude API timeout on attempt %d", attempt)
            continue
        except anthropic.RateLimitError as exc:
            last_err = exc
            log.warning("Claude rate limit on attempt %d", attempt)
            continue
        except anthropic.AuthenticationError as exc:
            raise PoExtractionError(
                "Extractor authentication failed. Contact admin.",
                status=503,
            ) from exc
        except anthropic.BadRequestError as exc:
            # BadRequestError covers multiple root causes; the message must
            # tell them apart so the user knows what to fix.
            #   * credit balance exhausted → billing message (503)
            #   * per-request limit / quota exhausted → billing-adjacent (503)
            #   * genuinely bad PDF (too big, unreadable, unsupported) → PDF
            #     message (400) so the user knows to re-upload
            msg = str(exc).lower()
            if "credit balance" in msg or "insufficient" in msg or "low balance" in msg:
                raise PoExtractionError(
                    "Anthropic API credit balance is exhausted. "
                    "Top up at https://console.anthropic.com/settings/billing "
                    "and retry.",
                    status=503,
                ) from exc
            if "quota" in msg or "usage limit" in msg or "spend limit" in msg:
                raise PoExtractionError(
                    "Anthropic API usage/quota limit reached. Check plan "
                    "limits at https://console.anthropic.com/settings/limits.",
                    status=503,
                ) from exc
            raise PoExtractionError(
                "The PDF could not be processed (too large or unreadable).",
                status=400,
            ) from exc
        except anthropic.APIStatusError as exc:
            last_err = exc
            log.warning("Claude API status error on attempt %d: %s", attempt, exc)
            continue

    raise PoExtractionError(
        "Extraction failed after retries. Please try again or fill in manually.",
        status=503,
    ) from last_err


# ── Schema coercion ───────────────────────────────────────────────────────────

def _normalize_contract(raw: dict[str, Any]) -> dict[str, Any]:
    items_raw = raw.get("items") or []
    items: list[dict[str, Any]] = []
    for i, item in enumerate(items_raw, start=1):
        if not isinstance(item, dict):
            continue
        items.append({
            "line": _coerce_int(item.get("line") or i),
            "nsn": str(item.get("nsn", "") or ""),
            "part_no": str(item.get("part_no", "") or "").strip(),
            "ncage": str(item.get("ncage", "") or ""),
            "description": str(item.get("description", "") or "").strip(),
            "qty": _coerce_number(item.get("qty", 0)),
            "unit": str(item.get("unit", "EA") or "EA"),
            "unit_price": _coerce_number(item.get("unit_price", 0)),
            "extended": _coerce_number(item.get("extended", 0)),
            "delivery_lead_time": str(item.get("delivery_lead_time", "") or ""),
        })
    return {
        "contract_no": str(raw.get("contract_no", "") or "").strip(),
        "contract_date": str(raw.get("contract_date", "") or ""),
        "client_reference": str(raw.get("client_reference", "") or ""),
        "title": str(raw.get("title", "") or ""),
        "currency": (str(raw.get("currency", "USD") or "USD")).upper(),
        "delivery_date": str(raw.get("delivery_date", "") or ""),
        "delivery_address": raw.get("delivery_address") or {"name": "", "lines": []},
        "incoterms": str(raw.get("incoterms", "") or "").strip(),
        "payment_terms": str(raw.get("payment_terms", "") or "").strip(),
        "contracting_authority": raw.get("contracting_authority") or {},
        "technical_authority": raw.get("technical_authority") or {},
        "contractor_rep": raw.get("contractor_rep") or {},
        "items": items,
        "flow_down_clauses": _curate_flow_down(raw.get("flow_down_clauses") or []),
    }


# Pattern for an isolated DND clause identifier, e.g. "B7500C (2006-06-16)",
# "C2000C (2007-11-30) Taxes - Foreign-based Contractor.", "D5540C". If the
# string is mostly this with a token/two of description, it's a raw clause
# dump that doesn't belong on a supplier PO.
_CLAUSE_ID_RE = re.compile(r"^\s*[A-Z]\d{4}[A-Z]\b")


def _curate_flow_down(items: list[Any]) -> list[str]:
    """Belt-and-suspenders filter: drop entries that look like raw clause-ID
    dumps even if the prompt told the LLM not to emit them. Cap the list at
    8 bullets — past that, the supplier stops reading."""
    out: list[str] = []
    for c in items:
        text = str(c or "").strip()
        if not text:
            continue
        if _CLAUSE_ID_RE.match(text) and len(text) < 80:
            # e.g. "B7500C (2006-06-16) Excess Goods." — raw clause, drop
            continue
        out.append(text)
        if len(out) >= 8:
            break
    return out


def _normalize_quote(raw: dict[str, Any]) -> dict[str, Any]:
    items_raw = raw.get("items") or []
    items: list[dict[str, Any]] = []
    for i, item in enumerate(items_raw, start=1):
        if not isinstance(item, dict):
            continue
        items.append({
            "line": _coerce_int(item.get("line") or i),
            "part_no": str(item.get("part_no", "") or "").strip(),
            "description": str(item.get("description", "") or "").strip(),
            "qty": _coerce_number(item.get("qty", 0)),
            "unit": str(item.get("unit", "EA") or "EA"),
            "unit_price": _coerce_number(item.get("unit_price", 0)),
            "extended": _coerce_number(item.get("extended", 0)),
            "lead_time": str(item.get("lead_time", "") or ""),
        })
    return {
        "quote_no": str(raw.get("quote_no", "") or "").strip(),
        "quote_date": str(raw.get("quote_date", "") or ""),
        "expires_on": str(raw.get("expires_on", "") or ""),
        "supplier": raw.get("supplier") or {"name": "", "lines": []},
        "sold_to": raw.get("sold_to") or {},
        "sales_rep": str(raw.get("sales_rep", "") or ""),
        "sales_email": str(raw.get("sales_email", "") or ""),
        "currency": (str(raw.get("currency", "USD") or "USD")).upper(),
        "payment_terms": str(raw.get("payment_terms", "") or ""),
        "incoterms": str(raw.get("incoterms", "") or ""),
        "items": items,
        "freight": _coerce_number(raw.get("freight", 0)),
        "misc_charges": _coerce_number(raw.get("misc_charges", 0)),
        "taxes": _coerce_number(raw.get("taxes", 0)),
        "net_total": _coerce_number(raw.get("net_total", 0)),
    }


# ── Public API ────────────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    data: dict[str, Any]
    warnings: list[str]


def extract_contract(pdf_bytes: bytes, filename: str = "") -> ExtractionResult:
    """Extract structured fields from an awarded-contract PDF."""
    validate_pdf(pdf_bytes, role="contract")
    warnings: list[str] = []
    if looks_like_scan(pdf_bytes):
        warnings.append(
            "Contract appears to be a scanned image. Extraction may be incomplete — please verify all fields.",
        )
    role = detect_role(pdf_bytes)
    if role == "quote":
        warnings.append(
            "This file looks more like a supplier quote than a contract — please double-check the upload order.",
        )
    log.info("po.extract.contract.start filename=%r size=%d", filename, len(pdf_bytes))
    raw = _call_claude(pdf_bytes, CONTRACT_PROMPT)
    data = _normalize_contract(raw)
    log.info(
        "po.extract.contract.end contract_no=%r items=%d warnings=%d",
        data.get("contract_no"), len(data.get("items", [])), len(warnings),
    )
    return ExtractionResult(data=data, warnings=warnings)


def extract_quote(pdf_bytes: bytes, filename: str = "") -> ExtractionResult:
    """Extract structured fields from a supplier-quote PDF."""
    validate_pdf(pdf_bytes, role="quote")
    warnings: list[str] = []
    if looks_like_scan(pdf_bytes):
        warnings.append(
            "Quote appears to be a scanned image. Extraction may be incomplete — please verify all fields.",
        )
    role = detect_role(pdf_bytes)
    if role == "contract":
        warnings.append(
            "This file looks more like a contract than a supplier quote — please double-check the upload order.",
        )
    log.info("po.extract.quote.start filename=%r size=%d", filename, len(pdf_bytes))
    raw = _call_claude(pdf_bytes, QUOTE_PROMPT)
    data = _normalize_quote(raw)
    log.info(
        "po.extract.quote.end quote_no=%r items=%d warnings=%d",
        data.get("quote_no"), len(data.get("items", [])), len(warnings),
    )
    return ExtractionResult(data=data, warnings=warnings)
