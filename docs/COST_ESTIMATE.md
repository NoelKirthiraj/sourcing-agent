# Cost Estimate: CanadaBuys → CFlow Sourcing Intake Agent

**Date:** March 2026  
**Prepared for:** Iyakkam Client Engagement

---

## TL;DR

**This agent costs ~$0/month to run.** The entire stack fits within free tiers. The only real cost is developer time to set it up and maintain it. This is one of the sharpest cost differentials in the competitive landscape: MERX charges ~$500+/year for email alerts. This agent does more, for nothing, indefinitely.

---

## Monthly Infrastructure Costs (MVP)

| Category | Service | Cost/Month | Notes |
|----------|---------|-----------|-------|
| Scheduling + CI | GitHub Actions | **$0** | Free tier: 2,000 min/month. Agent uses ~132 min/month (6 min × 22 days). 93% headroom. |
| Browser automation | Playwright / Chromium | **$0** | Open source. Runs inside GitHub Actions runner. |
| HTTP client | HTTPX | **$0** | Open source Python library. |
| State persistence | JSON file + GHA cache | **$0** | `actions/cache` is free. File is ~10KB even after a year of runs. |
| Notifications | Slack Incoming Webhooks | **$0** | Free, no rate limits for a single workspace integration. |
| Notifications | SMTP (Gmail/Outlook) | **$0** | Standard email account. If using Gmail: free app password. |
| Source portal | CanadaBuys | **$0** | Public government portal, no authentication required. |
| Target system | CFlow | **$0** | Client already pays for CFlow. API access is included in their subscription. |
| Log storage | GitHub Actions Artifacts | **$0** | 30-day retention, free tier includes 500MB storage. |
| Domain / SSL | None required | **$0** | Agent has no public-facing endpoint. |
| **Total** | | **$0/month** | |

---

## Annual Infrastructure Cost

| Scenario | Annual Cost |
|----------|-------------|
| Current (manual Data Miner + CFlow entry) | ~$0 infrastructure + **~260 hours/year** of human labour |
| This agent | **$0** infrastructure + **~3 hours/year** maintenance |
| MERX (for comparison) | **~$500–600 CAD/year** subscription + still requires human to initiate CFlow |
| Publicus.ai (for comparison) | **~$2,400–6,000 CAD/year** enterprise tier + still no CFlow integration |

---

## Development Investment

### Initial Build (already complete)
The agent code has been written. What remains is setup and validation.

| Phase | Estimated Time | Who |
|-------|---------------|-----|
| Environment setup + credentials | 1 hour | Developer |
| `discover_fields.py` — CFlow field mapping | 1 hour | Developer + client (to verify fields) |
| Dry-run validation + selector fixes (if needed) | 2–4 hours | Developer |
| CFlow integration test (draft records) | 1 hour | Developer + client (to sign off) |
| Notification setup (Slack or email) | 30 min | Developer |
| GitHub Actions deployment | 1 hour | Developer |
| 2-week parallel run oversight | 30 min/week | Developer |
| Data Miner retirement + SOP update | 30 min | Client |
| **Total to go-live** | **~8–10 hours** | |

At a typical developer rate of $150 CAD/hour, total setup cost: **~$1,200–1,500 CAD (one-time).**

### Testing Suite Build (optional but recommended)
Writing the unit + integration tests from `TESTING.md`:

| Scope | Estimated Time |
|-------|---------------|
| Unit tests (state, payload mapping, helpers) | 3 hours |
| Integration tests (mocked CFlow + mocked HTML) | 4 hours |
| HTML fixtures capture + setup | 1 hour |
| CI integration (add test step to GHA workflow) | 30 min |
| **Total** | **~8.5 hours** |

At $150 CAD/hour: **~$1,275 CAD (one-time).** Recommended — pays back the first time a portal HTML change is caught by tests before it silently produces zero results.

---

## Ongoing Maintenance Estimate

| Activity | Estimated Time | Frequency | Annual Hours |
|----------|---------------|-----------|-------------|
| CSS selector update (CanadaBuys HTML change) | 1–2 hours | 1–2× per year | ~2–4 hrs |
| CFlow field mapping update (if workflow changes) | 30 min | Rarely | ~0.5 hrs |
| Dependency updates (`pip install --upgrade`) | 30 min | Quarterly | ~2 hrs |
| GitHub Actions runner compatibility check | 15 min | Quarterly | ~1 hr |
| Notification config update (Slack channel change, etc.) | 15 min | Rarely | ~0.25 hrs |
| **Total annual maintenance** | | | **~6–8 hours/year** |

At $150 CAD/hour: **~$900–1,200 CAD/year** for maintenance (worst case, if CanadaBuys changes HTML twice).

---

## Value Delivered vs. Cost

### Human Labour Eliminated

| Task | Time per occurrence | Frequency | Annual hours saved |
|------|--------------------|-----------|--------------------|
| Run Data Miner Chrome plugin | 5 min | Daily (weekdays) | ~18 hours |
| Copy fields into CFlow form (per tender) | 3 min × avg 5 tenders/day | Daily | ~55 hours |
| Manual deduplication check | 2 min/day | Daily | ~7 hours |
| **Total eliminated** | | | **~80 hours/year** |

At an internal loaded labour rate of $80 CAD/hour (conservative):
**Annual value delivered: ~$6,400 CAD/year**

### ROI Calculation

| | One-Time | Annual |
|-|---------|--------|
| Setup cost | ~$1,500 CAD | — |
| Testing suite (optional) | ~$1,275 CAD | — |
| Maintenance | — | ~$900–1,200 CAD |
| Infrastructure | $0 | $0 |
| **Total cost** | **~$2,775 CAD** | **~$1,050 CAD** |
| **Value delivered** | — | **~$6,400 CAD** |
| **Net annual benefit** | — | **~$5,350 CAD** |
| **Payback period** | **~6 months** | |
| **3-year ROI** | **~480%** | |

---

## Scaling Projections

This agent has essentially no scaling cost — GitHub Actions free tier absorbs all realistic growth scenarios.

| Scale | Monthly Cost | What Changes |
|-------|-------------|-------------|
| **Current: ~5 tenders/day, 1 portal** | $0 | Baseline |
| **10× tenders: ~50/day (more categories)** | $0 | Run time increases from ~6 to ~20 min; still well within 2,000 min/month free tier |
| **Add 2 more portals (MERX, Ontario Tenders)** | $0 | 3 parallel scraper classes; run time ~30 min; still free tier |
| **100× tenders: ~500/day** | $0–5 | May exceed GHA free tier (~660 min/month). Upgrade to $4 USD/month Pro plan or self-host a runner. |
| **Multi-client deployment (5 clients)** | $0–20 | GitHub Actions matrix: 5 parallel jobs × 6 min = 30 min/day × 22 days = 660 min/month. Pro plan at $4/mo covers all 5 clients. |

**Key insight:** The cost curve is flat until ~10× current volume, then trivially small ($4–5 USD/month). This is a fundamentally different cost profile from SaaS competitors, which charge per seat or per portal regardless of volume.

---

## Cost Optimization Tips

1. **Never pay for GitHub Actions at current scale.** The free tier handles the workload with 93% headroom. Revisit only if expanding to 5+ portals or 10+ clients simultaneously.

2. **Use Gmail App Password for SMTP.** No SendGrid or Postmark account needed. Gmail's free SMTP handles 1 email/day trivially. (Note: for higher volume or branded `@company.com` sender, Resend.com free tier handles 3,000 emails/month.)

3. **Avoid adding a database until there's a clear need.** The JSON state file costs $0 and handles millions of records without performance issues (it's a simple key-value lookup). The trigger to add SQLite is: "we need to query or report on historical data" — not "it feels more professional."

4. **Don't upgrade Playwright features.** The base `playwright install chromium` installs only Chromium (~120MB). Do not install `--with-deps` locally (only in CI) to avoid bloating developer machines.

5. **Cache the Chromium install in GitHub Actions.** The `pip` cache step already covers Python packages. Add a `playwright-browsers` cache key to cut ~90 seconds off each run — meaningful at scale.

---

## Break-Even vs. MERX Subscription

| | MERX | This Agent |
|-|------|-----------|
| Annual subscription | ~$500 CAD | $0 |
| Workflow initiation | Manual (human) | Automated |
| CFlow integration | None | Native |
| Break-even vs. MERX | — | **Day 1** |

The agent pays for its setup cost in under 6 months purely from labour savings. Compared to MERX, it's free from day one and delivers more functionality.
