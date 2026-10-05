# Scope — Supplier Matching & Outreach

**Status:** v1 scope locked, 5 October 2026
**Author:** RAD Global engineering

## Locked decisions (v1)

| Decision | Choice |
|---|---|
| Matching | **SQL retrieval only** — no AI ranking |
| Trigger | **Button**, pressed by the associate |
| Shortlist | **Top 5 pre-checked**, "show more" reveals the rest unchecked |
| Draft signature | **The assigned associate** |
| External search | **In scope**, kept separate until a human vets it |
| "Contacted" | **Generating a draft counts** — updates `inquiry_count` and `last_contact` |

Connects the vendor registry to the tender pipeline: when an associate accepts a tender, suggest the best-fit suppliers from our records, optionally search the web for ones we don't have, let the associate check/uncheck, and produce email drafts for manual sending.

---

## 1. Where it hooks in

The trigger point already exists. `db.accept_tender()` sets `status='accepted'`, stamps `reviewed_at`, and assigns an associate by round-robin. Everything below hangs off that moment.

```
Tender accepted  →  [1] Shortlist from registry  →  [3] Associate reviews
                    [2] Optional web search          checkboxes
                                                  →  [4] Email drafts (copy/paste)
                                                  →  [5] Logged as outreach
```

Nothing in the existing accept path changes. The feature is additive.

---

## 2. Matching design

### 2.1 The signals we have

**On an accepted tender:** `solicitation_title`, `gsin_description`, `summary_of_contract`, `requirements` (line items with part numbers and quantities), `mandatory_criteria`, `client`.

**On a vendor:** `rfp_categories[]` (coarse — "Aerospace/Aircraft"), `products_quoted[]` (free text), and `products_by_vendor` rows carrying a specific product plus an `rfp_code`.

**The bridge:** `rfp_categories.keywords[]` — already populated with terms like `aircraft; aerospace; jet` and `navy; submarine; ship`. **No code has ever read this column.** It exists precisely to map tender language onto vendor categories, and it is why this feature is cheaper to build than it looks.

### 2.2 Retrieval and scoring (SQL only)

**Retrieval**

- Match tender text against `rfp_categories.keywords` to derive candidate categories
- Select vendors whose `rfp_categories` overlap those candidates (the existing GIN index makes this fast)
- Separately, full-text match tender title and requirement lines against `products_by_vendor.product`
- Union the two → typically 10–60 candidates from ~900

**Scoring** — deterministic, tunable, explainable:

| Signal | Points | Why |
|---|---|---|
| Product-level match (`products_by_vendor.product`) | **3** each | Specific — this supplier has quoted this kind of item before |
| Category match (`rfp_categories`) | **1** each | Coarse — right sector, unproven on the item |
| Has bid history (`bid_count > 0`) | **+1** once | A supplier who has actually bid is a safer ask than one who never has |

Ordered by score descending, then by company name for stable ties. Top 5 pre-checked; the rest behind "show more", unchecked.

**Rationale is generated from the match itself, not written by a model:**

> *Category: Aerospace/Aircraft (matched "aircraft"). Products: Aircraft Towbar, Landing Gear Kit. 3 prior bids.*

**Human review.** The associate checks, unchecks, or adds vendors by search. Their decision is what counts — the score is a starting point, never an action.

### 2.3 What we give up, and what we gain

Dropping AI ranking loses the semantic cases: a tender for "aircraft ground support equipment" won't surface a supplier whose only listed product is "towbar" unless a keyword bridges them. That is a real limitation and it is **entirely determined by keyword coverage**, which is why Phase 0 matters more under this design, not less.

What we gain is worth having. The rationale is now a **statement of fact rather than a model's claim** — it names the exact keyword and product rows that caused the match, so an associate can verify it in one glance and an auditor can reproduce it. Retrieval is free, instant, and identical every time it runs. And if coverage turns out to be the binding constraint, enriching keywords is a data task the sourcing team can do themselves, with no engineering involved.

AI ranking stays available as a later upgrade. It would slot in behind the same retrieval layer without rework.

### 2.4 Known risk — the keywords column is untested

`rfp_categories.keywords` has never run in production. If coverage is thin, Stage 1 returns few candidates and the whole funnel underperforms.

**Mitigation, before any UI work:** run Stage 1 offline against the ~160 historically accepted tenders and measure how many produce a usable candidate set. That is half a day and it de-risks the entire feature. If coverage is poor, the fix is enriching keywords — a data task, not an engineering one.

**Fallback if a tender matches nothing:** widen to the tender's procurement category, or surface "no confident match" rather than padding the list with noise. Suggesting the wrong supplier is worse than suggesting none.

---

## 3. External supplier search

A separate, explicitly-triggered action — never automatic, because it costs money per use and returns unverified results.

- Uses Claude's web search to find suppliers matching the tender requirement, biased to Canadian firms able to sell to the federal government
- Results deduplicated against the registry by normalised company name and domain
- Returns company, domain, why it looks relevant, and a source link
- Displayed in a **visually distinct section**, clearly marked unverified

**These must never be auto-added to the vendor registry.** An external result is a lead, not a supplier. Adding one is a deliberate second click, after a human has looked at it.

---

## 4. Email drafts

For each selected supplier, generate a draft: subject, body, and the recipient address pulled from the vendor's `emails[]`. Signed by the **assigned associate**, taken from `tenders.assigned_associate`.

**Recommendation: template-only for v1, no AI.** We already have `summary_of_contract` extracted from the solicitation, plus the title, solicitation number and closing date. A fixed template slotting those in produces a correct, consistent email at zero cost — and it keeps v1 consistent with the SQL-only decision for matching.

The trade-off is that it will read somewhat formulaic. Adding an AI-written requirement paragraph costs ~$0.01 per draft and reads better. Easy to add later without touching anything else; say the word if you'd rather have it from the start.

**Delivery is copy/paste.** Each draft gets a copy button; no SMTP, no sending, no mailbox integration. Generating a draft is what counts as contact — it increments `inquiry_count` and sets `last_contact`.

---

## 5. Data model changes

Two new tables. No changes to existing ones.

```sql
tender_vendor_suggestions
  id, tender_id, vendor_id (NULL for external results),
  external_name, external_domain, external_source_url,
  score, rationale, origin ('internal'|'external'),
  selected BOOLEAN, created_at

tender_outreach
  id, tender_id, vendor_id, contact_email,
  subject, body, status ('draft'|'copied'),
  created_by, created_at
```

**A welcome side effect:** `vendors.inquiry_count` and `vendors.last_contact` are currently imported from the spreadsheet and never written to by the system. Creating an outreach record updates both — so those fields finally become live, and the registry starts reflecting real activity rather than a snapshot of a spreadsheet.

---

## 6. API and UI

**Endpoints** (4 new):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/tenders/<id>/suggest-vendors` | Run stages 1–2, return ranked shortlist |
| `POST` | `/api/tenders/<id>/suggest-vendors/external` | Web search for suppliers not in the registry |
| `POST` | `/api/tenders/<id>/outreach` | Generate drafts for the selected suppliers |
| `GET` | `/api/tenders/<id>/outreach` | Retrieve existing drafts |

**Reuse the async job pattern from PR #61.** The AI ranking takes 10–30 seconds and a web search can take longer — both risk Railway's 300-second edge timeout. `po_jobs.py` already solves this and should be generalised rather than duplicated.

**UI** — a new panel in the tender detail view, visible only when `status='accepted'`: the ranked list with checkboxes and rationales, a "Find external suppliers" button, a "Generate drafts" button, and draft cards with copy buttons.

---

## 7. Effort and cost

| Phase | Work | Estimate |
|---|---|---|
| **0** | Keyword coverage dry-run against historical tenders | **0.5 day** |
| **1** | Schema, retrieval + scoring, API | 2 days |
| **2** | Review UI — list, checkboxes, rationale, show more | 2 days |
| **3** | Email drafts (template) + copy UI + outreach logging | 1.5 days |
| **4** | External web search + dedup + distinct display | 2 days |
| **5** | Tests, end-to-end verification | 1.5 days |
| | **Total** | **~9.5 days** |

Phases 1–3 deliver a working internal feature in about **six days**. Phase 4 is separable.

**Running cost**, assuming ~100 accepted tenders a month:

| Item | Per use | Monthly |
|---|---|---|
| Supplier matching | **$0** (SQL) | **$0** |
| Email drafts | **$0** (template) | **$0** |
| External search (occasional, button-triggered) | ~$0.10 | ~$3 |
| | | **~$3/month** |

The core flow costs nothing to run. The only AI spend is external search, and only when someone asks for it.

---

## 8. Remaining open item

**Email draft style** — template-only (free, consistent, slightly formulaic) or template plus an AI-written requirement paragraph (~$0.01 per draft, reads better). Recommendation is template-only for v1; see Section 4. Everything else is decided.

---

## 9. Sequencing note

This feature gets materially better **after** the CanadaBuys data feed migration. The feed carries structured classification — GSIN codes, UNSPSC codes, and procurement category — where the current scraper captures only a free-text description. Structured codes are a far stronger matching signal than prose.

Nothing blocks starting now, and the matching layer would not need rewriting. But if both are planned, doing the feed first means the keyword work in Phase 0 is done against better input.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| **`keywords` coverage too thin for useful retrieval** | **The binding risk under SQL-only matching.** Phase 0 dry-run before committing to the build. If coverage is poor, enriching keywords is a data task for the sourcing team, not engineering work |
| Semantic misses — right supplier, no shared keyword | Accepted limitation of v1. Mitigated by keyword enrichment; AI ranking remains the upgrade path and slots in behind the same retrieval layer |
| Vendor contact emails missing or stale | Audit `emails[]` coverage in Phase 0 alongside keywords; a supplier with no address can be matched but not contacted |
| External results suggest firms that can't supply federally | Marked unverified, visually separated, never auto-added |
| Associates over-trust the shortlist | Factual rationale on every row naming the keyword and products that matched; only the top 5 are pre-checked; selection is always a human act |
| Draft generated but never actually sent | `inquiry_count` will slightly overstate real contact. Accepted trade-off for v1 — avoids an extra confirmation click |
