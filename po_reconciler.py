"""
PO reconciler — turns (extracted contract, extracted quote) into a PO draft.

Core rules:
  1. Only items that appear on the awarded CONTRACT get PO lines.
  2. PO unit price = QUOTE price (supplier's price to RAD), not contract price.
  3. qty × unit_price MUST equal extended (within $0.01). Mismatches are warnings.
  4. Items missing a quote match are kept but flagged "needs review."
  5. Currency mismatch between contract and quote is a blocking warning.

The output `draft` is the dict the renderer + frontend consume.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)

CENT = 0.011  # tolerance for math checks — rounding slack

# Incoterms 2020 codes where the supplier hands off goods at their own facility
# or origin point — the buyer's PO should not carry a SHIP TO address under
# these terms, because the buyer's carrier collects from the supplier.
# Buyer-side terms (DAP, DPU, DDP, CIP, CPT, CIF, CFR) — supplier delivers
# to the buyer's destination, SHIP TO is meaningful.
_SELLER_SIDE_INCOTERMS = {"EXW", "EXWORKS", "FCA", "FAS", "FOB"}


def _is_seller_side_incoterm(incoterms: str) -> bool:
    """True when the incoterm leaves the goods at the supplier's facility/origin."""
    if not incoterms:
        return False
    # Match the first token of the incoterm; ignore the named place that follows.
    head = re.split(r"[\s,/]+", incoterms.strip().upper(), maxsplit=1)[0]
    return head in _SELLER_SIDE_INCOTERMS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_part(part: str) -> str:
    """Normalize a part number for comparison. Uppercase, strip non-alphanumerics."""
    return re.sub(r"[^A-Za-z0-9]", "", part or "").upper()


def _find_quote_match(
    contract_item: dict[str, Any],
    quote_items: list[dict[str, Any]],
    used_indices: set[int],
) -> Optional[int]:
    """Find the best quote-item index matching a contract item.

    Order: exact part-number match → normalized part-number match → description
    substring match → None. `used_indices` prevents matching the same quote
    line twice.
    """
    c_part = contract_item.get("part_no", "") or ""
    c_desc = (contract_item.get("description", "") or "").lower()

    # Pass 1: exact case-insensitive part match
    for i, q in enumerate(quote_items):
        if i in used_indices:
            continue
        if c_part and q.get("part_no", "").lower() == c_part.lower():
            return i

    # Pass 2: normalized (strip dashes/spaces) part match
    c_norm = _normalize_part(c_part)
    if c_norm:
        for i, q in enumerate(quote_items):
            if i in used_indices:
                continue
            if _normalize_part(q.get("part_no", "")) == c_norm:
                return i

    # Pass 3: description token overlap (≥2 shared tokens of length ≥3)
    c_tokens = {t for t in re.split(r"\W+", c_desc) if len(t) >= 3}
    if c_tokens:
        best_idx = None
        best_overlap = 0
        for i, q in enumerate(quote_items):
            if i in used_indices:
                continue
            q_desc = (q.get("description", "") or "").lower()
            q_tokens = {t for t in re.split(r"\W+", q_desc) if len(t) >= 3}
            overlap = len(c_tokens & q_tokens)
            if overlap >= 2 and overlap > best_overlap:
                best_overlap = overlap
                best_idx = i
        if best_idx is not None:
            return best_idx

    return None


def _math_check(qty: float, unit_price: float, extended: float) -> bool:
    expected = round(qty * unit_price, 2)
    return abs(expected - round(extended, 2)) <= CENT


# ── Reconciliation ────────────────────────────────────────────────────────────

@dataclass
class ReconcileResult:
    draft: dict[str, Any]
    warnings: list[str]


_RAD_REF_RE = re.compile(r"\bRAD[-\s]?(\d{3,6})\b", re.IGNORECASE)


def _extract_rad_tender_from_text(text: str) -> Optional[str]:
    """Pull a RAD tender number (e.g. "RAD-7813", "RAD 7813", "rad7813")
    out of free text. Returns the numeric tender id as a string, or None."""
    if not text:
        return None
    m = _RAD_REF_RE.search(text)
    return m.group(1) if m else None


def _suggest_po_number(contract: dict[str, Any], tender_id: Optional[int]) -> str:
    """Derive a default PO number for the draft.

    RAD's PO numbering convention is "PO-RAD-<tender>-<seq>" (e.g.
    PO-RAD-7813-001). The trailing -001 is the sequence number for the
    first PO cut against a tender; subsequent POs against the same tender
    (split orders, additional suppliers) would increment to -002, -003.
    We always emit -001 here since the reconciler doesn't track prior
    POs; the operator can bump it manually before submit if needed.

    Heuristics in order of preference:
      1. PO-RAD-<tender_id>-001              (explicit tender link)
      2. PO-RAD-<from client_reference>-001  (DND contracts carry "RAD-NNNN"
                                              in their client_reference field;
                                              recognise it so the right
                                              format gets emitted even when
                                              the PO uploader UI doesn't
                                              currently pass tender_id)
      3. PO-<contract-no>                    (cleaned, last-resort fallback)
      4. PO-DRAFT
    """
    if tender_id:
        return f"PO-RAD-{tender_id}-001"
    # Try the contract's client_reference field — DND contracts include
    # the RAD internal tender number there (e.g. "RAD-7813").
    rad_num = _extract_rad_tender_from_text(contract.get("client_reference") or "")
    # Belt-and-suspenders: also scan the contract title in case the
    # reference was captured into a different field.
    if not rad_num:
        rad_num = _extract_rad_tender_from_text(contract.get("title") or "")
    if rad_num:
        return f"PO-RAD-{rad_num}-001"
    cno = (contract.get("contract_no") or "").strip()
    if cno:
        # Strip spaces / slashes for a filename-safe number
        safe = re.sub(r"[^A-Za-z0-9._-]", "-", cno)
        return f"PO-{safe}"
    return "PO-DRAFT"


def reconcile(
    contract: dict[str, Any],
    quote: dict[str, Any],
    *,
    tender_id: Optional[int] = None,
) -> ReconcileResult:
    """Build a PO draft by joining contract items to quote items.

    Returns (draft_dict, warnings[]). Warnings are user-readable strings; the
    frontend should display them as banners.
    """
    warnings: list[str] = []

    c_currency = (contract.get("currency") or "USD").upper()
    q_currency = (quote.get("currency") or c_currency).upper()
    if c_currency != q_currency:
        warnings.append(
            f"Currency mismatch: contract is in {c_currency} but quote is in {q_currency}. "
            "Cannot reconcile automatically — please handle manually.",
        )

    contract_items = contract.get("items") or []
    quote_items = quote.get("items") or []

    if not contract_items:
        warnings.append("No items were extracted from the contract. Add lines manually below.")
    if not quote_items:
        warnings.append("No items were extracted from the supplier quote. Pricing fell back to contract prices.")

    used_quote_idx: set[int] = set()
    po_lines: list[dict[str, Any]] = []

    for i, c_item in enumerate(contract_items, start=1):
        match_idx = _find_quote_match(c_item, quote_items, used_quote_idx)
        if match_idx is not None:
            q_item = quote_items[match_idx]
            used_quote_idx.add(match_idx)
            qty = c_item.get("qty") or q_item.get("qty") or 0
            unit_price = q_item.get("unit_price") or 0.0
            source = "quote"
            lead_time = q_item.get("lead_time") or c_item.get("delivery_lead_time", "")
            match_status = "matched"
            match_note = ""
            # Sanity: if quote part number is materially different, flag it.
            if (c_item.get("part_no") and q_item.get("part_no")
                    and _normalize_part(c_item["part_no"]) != _normalize_part(q_item["part_no"])):
                match_note = (
                    f"Matched by description — contract P/N '{c_item.get('part_no')}' "
                    f"differs from quote P/N '{q_item.get('part_no')}'."
                )
                match_status = "fuzzy"
        else:
            qty = c_item.get("qty") or 0
            unit_price = c_item.get("unit_price") or 0.0
            source = "contract"
            lead_time = c_item.get("delivery_lead_time", "")
            match_status = "unmatched"
            match_note = (
                "No matching item in the supplier quote — using contract price. "
                "Verify the supplier intends to fulfill this line."
            )
            if c_item.get("part_no"):
                warnings.append(
                    f"Contract line P/N '{c_item['part_no']}' has no matching quote line.",
                )

        extended_raw = round(float(qty) * float(unit_price), 2)
        # If the source item had an extended value and it disagrees with qty×price,
        # surface the conflict — but keep our computed value as authoritative.
        math_ok = True
        if source == "quote":
            q_ext = float(quote_items[match_idx].get("extended") or 0)
            if q_ext and not _math_check(float(qty), float(unit_price), q_ext):
                math_ok = False
                warnings.append(
                    f"Line {i}: quote arithmetic looks off (qty {qty} × {unit_price} ≠ {q_ext}). Please verify.",
                )
        elif source == "contract":
            c_ext = float(c_item.get("extended") or 0)
            if c_ext and not _math_check(float(qty), float(unit_price), c_ext):
                math_ok = False
                warnings.append(
                    f"Line {i}: contract arithmetic looks off (qty {qty} × {unit_price} ≠ {c_ext}). Please verify.",
                )

        po_lines.append({
            "line": i,
            "part_no": c_item.get("part_no") or (quote_items[match_idx].get("part_no") if match_idx is not None else ""),
            "nsn": c_item.get("nsn", ""),
            "ncage": c_item.get("ncage", ""),
            "description": c_item.get("description") or (quote_items[match_idx].get("description") if match_idx is not None else ""),
            "qty": float(qty),
            "unit": c_item.get("unit") or (quote_items[match_idx].get("unit") if match_idx is not None else "EA"),
            "unit_price": float(unit_price),
            "extended": extended_raw,
            "lead_time": lead_time,
            "price_source": source,
            "match_status": match_status,
            "match_note": match_note,
            "math_ok": math_ok,
        })

    # Subtotal and total (no taxes by default — DND contracts typically state taxes extra)
    subtotal = round(sum(line["extended"] for line in po_lines), 2)
    total = subtotal  # extension point for freight/taxes later

    supplier = quote.get("supplier") or {}
    draft: dict[str, Any] = {
        "po_number": _suggest_po_number(contract, tender_id),
        "po_date": "",  # frontend or user fills today
        "tender_id": tender_id,
        "contract_no": contract.get("contract_no", ""),
        "contract_date": contract.get("contract_date", ""),
        "client_reference": contract.get("client_reference", ""),
        "title": contract.get("title", ""),
        "currency": c_currency,
        "quote_no": quote.get("quote_no", ""),
        "quote_date": quote.get("quote_date", ""),
        "supplier": {
            "name": supplier.get("name", ""),
            "lines": supplier.get("lines", []) or [],
            "cage_code": supplier.get("cage_code", ""),
            "duns": supplier.get("duns", ""),
            "attention": quote.get("sales_rep", ""),
            "email": quote.get("sales_email", ""),
        },
        "buyer": {
            "name": "RAD Global Procurement Inc.",
            "lines": ["735 Provencher Blvd", "Brossard, Quebec J4W 1Y5", "Canada"],
            "email": "inq@rgpmail.com",
            "phone": "+1 514 900 3899",
        },
        # Incoterms and payment terms are contract terms — the contract wins,
        # the quote is fallback only. Avoids the prior bug where the LLM
        # quote-extractor echoed schema example values into real outputs.
        "incoterms": (contract.get("incoterms") or quote.get("incoterms") or "").strip(),
        "payment_terms": (contract.get("payment_terms") or quote.get("payment_terms") or "").strip(),
        "delivery_date": contract.get("delivery_date", ""),
        # SHIP TO is meaningful only when delivery is buyer-side (DAP/DDP/CIP/CPT).
        # Under FCA/FOB/EXW/FAS the supplier hands off at their own facility, so
        # populating SHIP TO contradicts the incoterm. The renderer suppresses
        # the block when ship_to.lines is empty.
        "ship_to": (
            {
                "name": (contract.get("delivery_address") or {}).get("name", ""),
                "lines": (contract.get("delivery_address") or {}).get("lines", []),
            }
            if not _is_seller_side_incoterm(
                (contract.get("incoterms") or quote.get("incoterms") or "")
            )
            else {"name": "", "lines": []}
        ),
        "items": po_lines,
        "subtotal": subtotal,
        "total": total,
        "tax_note": "Taxes: As Applicable",
        "flow_down_clauses": contract.get("flow_down_clauses", []) or [],
        "authorized_by": {
            "name": "Edouard Radovic",
            "title": "Managing Director",
        },
        "notes": "",
    }

    log.info(
        "po.reconcile contract_items=%d quote_items=%d matched=%d unmatched=%d warnings=%d",
        len(contract_items), len(quote_items),
        sum(1 for l in po_lines if l["match_status"] in ("matched", "fuzzy")),
        sum(1 for l in po_lines if l["match_status"] == "unmatched"),
        len(warnings),
    )
    return ReconcileResult(draft=draft, warnings=warnings)


def validate_draft_math(draft: dict[str, Any]) -> list[str]:
    """Server-side check used by the generate endpoint before rendering.

    Returns a list of validation errors. Empty list = OK to render.
    """
    errors: list[str] = []
    items = draft.get("items") or []
    if not items:
        errors.append("PO has no line items.")
        return errors

    computed_subtotal = 0.0
    for line in items:
        try:
            qty = float(line.get("qty", 0))
            unit_price = float(line.get("unit_price", 0))
            extended = float(line.get("extended", 0))
        except (TypeError, ValueError):
            errors.append(f"Line {line.get('line', '?')}: non-numeric value.")
            continue
        if qty <= 0:
            errors.append(f"Line {line.get('line', '?')}: quantity must be greater than zero.")
        if unit_price < 0:
            errors.append(f"Line {line.get('line', '?')}: unit price cannot be negative.")
        if not _math_check(qty, unit_price, extended):
            errors.append(
                f"Line {line.get('line', '?')}: qty × unit_price ({qty} × {unit_price} = "
                f"{round(qty * unit_price, 2)}) does not match extended ({extended})."
            )
        computed_subtotal += round(qty * unit_price, 2)

    declared_subtotal = float(draft.get("subtotal", 0))
    if abs(round(computed_subtotal, 2) - round(declared_subtotal, 2)) > CENT:
        errors.append(
            f"Subtotal mismatch: declared {declared_subtotal}, computed {round(computed_subtotal, 2)}."
        )

    if not draft.get("po_number"):
        errors.append("PO number is required.")
    if not (draft.get("supplier") or {}).get("name"):
        errors.append("Supplier name is required.")

    return errors
