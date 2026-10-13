# Migration Runbook — RAD Global accounts → Client accounts

**Audience:** the person performing the migration (you)
**Prepared:** 13 October 2026
**Companion documents:**
- `CLIENT_DEPLOYMENT_PLAN.md` — *what* the accounts are and what they cost
- `CLIENT_BILLING_SETUP.md` — what the client does. **You create all four accounts; they attach payment.** Send it with the credentials once Section 3.1 is done, not before — it refers to logins they will not have yet, and Railway is the one they need to act on urgently.

This document is *how* to move onto those accounts.

---

## 0. Read this first

Four services hold the system today, all under personal RAD Global accounts:

| Service | What it holds now | Account today |
|---|---|---|
| **GitHub** | The code, the scheduled job, 4 secrets | `NoelKirthiraj/sourcing-agent` |
| **Railway** | The API process **and the PostgreSQL database** | Personal |
| **Vercel** | The dashboard (static) | Personal |
| **Anthropic** | The API key that does all AI extraction | Personal |

**The database is the only thing in this list that cannot be recreated.** Code can be re-pushed, hosts can be rebuilt, keys can be reissued. The Postgres database holds every staged tender, every associate assignment, the vendor registry, generated POs, supplier shortlists and outreach drafts. Section 4 is the step to slow down on.

**Estimated time:** 2.5–4 hours of focused work, plus waiting on account verification emails. It is not reversible in one click, so do it on a day when nobody needs the dashboard.

**Do not delete anything from the old accounts until Section 9 passes.** Keep them alive and paid for at least two weeks after cutover.

### Order of operations

The order is forced by what depends on what. Do not reorder it.

```
1. Anthropic ──────┐
                   ├──► 3. Railway (DB first, then API)
2. GitHub ─────────┘            │
                                ├──► send the client CLIENT_BILLING_SETUP.md
                                │         │
                                │         ├─ Railway card ───┐  needed before 4
                                │         └─ Anthropic card ─┼─┐ needed before 7.3
                                ▼                            │ │
                        4. Database migration  ◄──────────────┘ │
                                │                               │
                                ▼                               │
                        5. Vercel (needs the new API URL)       │
                                │                               │
                                ▼                               │
                        6. Rewire GitHub secrets + constants    │
                                │                               │
                                ▼                               │
                        7. Your local machine ──► 7.3 first run ◄┘
                                │
                                ▼
                        8/9. Verify, then decommission
```

### Who does what

**You create all four accounts and configure them. The client attaches a payment card to two of them.** They never touch the technical setup, and you never hold their card details.

That split has two consequences on your timeline, and both bite late if you miss them now:

| The client's step | Must land before | If it hasn't |
|---|---|---|
| **Railway card + Hobby upgrade** | **Section 4 — data migration** | The trial expires and deletes the database, taking the migrated data with it |
| **Anthropic card + $40 credit** | **Section 7.3 — first live run** | The API key is valid but has no credit; every extraction fails |

Everything else you can build while waiting. Tell the client about the Railway one specifically — it is the one that destroys data rather than merely blocking progress.

### The email address — decide this before you create anything

Every account is registered to one email address, and that address is the recovery route for all of them: password resets, billing receipts and security notices. Whoever controls it controls the lot. So the mailbox is not an incidental detail — it *is* the handover.

#### Option A — a dedicated Gmail you create, then hand over (default)

Create one new Gmail for this system, register all four accounts to it, and hand the client the Gmail at the end along with everything else. The appeal is that handover collapses to a single credential: once they own the mailbox, every downstream account follows, because recovery for all four runs through it.

**Creating it**

1. Sign up at **accounts.google.com** with a name that says what it is and whose it is — `<clientname>.sourcing@gmail.com`. Not a personal-sounding address: this will be sending billing alerts to a procurement team for years.
2. Google will ask for a **recovery phone number**. You will have to give one to get through signup — use yours, and treat replacing it as a required handover step rather than an optional one. Until it is replaced, Google can route account recovery to your phone, and the client cannot recover the account without you. This is the single loose end in Option A.
3. **Do not enable 2-Step Verification.** The client turns it on at handover, on their own device.
4. Put the Gmail password in the password manager immediately, alongside the four service credentials it protects.

**Handing it over** — the client does this from `CLIENT_BILLING_SETUP.md` Section 4, but it is on you to make sure it actually happens:

1. They change the Gmail password
2. They replace the recovery phone and recovery email with their own
3. They enable 2-Step Verification on their device
4. Only then are the four service accounts genuinely theirs

**One honest limitation.** `<clientname>.sourcing@gmail.com` is a consumer address, not one on the client's domain. It works, and plenty of small deployments run this way for years, but it is not provably the company's. If the client later wants everything on `procurement-tech@theircompany.com`, each service needs an email change and re-verification. Mention it now so the choice is theirs — and if they already have a company mailbox they are willing to provision, Option B is cleaner.

#### Option B — a shared mailbox on the client's own domain

Ask the client to create `procurement-tech@theircompany.com` and give you access for the duration. Register the four accounts there. At handover they change the password and your access ends cleanly, with no recovery phone to untangle and no consumer address to migrate off later.

Better if they can do it quickly. The reason it is not the default is that it depends on their IT, which is often the slowest thing in the whole migration.

If they will provision the mailbox but not share it: stay on a call while you create the accounts and have them read you each verification code. Forty minutes once, and ownership is correct from the start.

#### What not to do

Registering the accounts to a RAD Global address. It works today and leaves you holding the client's production accounts — every one then needs an email change plus re-verification later, Anthropic's organisation ownership being the awkward one, and until that is done the client's recovery route runs through you. If a deadline forces it, write it down as outstanding with a date.

> **Leave two-factor authentication off everywhere while you work** — the Gmail included. Enabled on your device, the client cannot get in and you will be reading them codes for months. They turn it on at handover, on hardware they control. GitHub organisations may require 2FA for members; if so, use a TOTP secret stored in the shared password manager rather than one person's phone.

### Before you start — have these ready

- [ ] Decided Option A (a Gmail you create) or Option B (the client's own mailbox) — and if B, access to it or a call booked
- [ ] The client's **legal company name** — it goes on the Anthropic organisation and appears on invoices
- [ ] A name for the GitHub organisation, agreed with the client
- [ ] A password manager vault to put every credential in as you create it
- [ ] The current **SAP Ariba** username and password — a dedicated account, not one a person also signs into
- [ ] Decision: does the client want a custom domain for the dashboard? (Section 5.4)
- [ ] `psql` and `pg_dump` installed locally — check with `pg_dump --version`

> If `pg_dump` is missing: `brew install libpq && brew link --force libpq` on macOS.

---

## 1. Anthropic — the AI key

Start here. Nothing else works without it, and a new organisation sometimes needs a manual review before the key will accept production traffic.

Anthropic bills on **prepaid credits**, not a monthly invoice, and the spend cap lives on a **Workspace** rather than on the organisation.

**You do steps 1, 2, 5 and 6. The client does 3 and 4 later**, from `CLIENT_BILLING_SETUP.md` Section 1 — they are the only steps that need a card.

1. **console.anthropic.com** → sign up with the mailbox from Section 0.
2. Organisation named with the **client's legal name** — it appears on invoices.
3. *(client)* Payment method added.
4. *(client)* $40 of credit bought, auto-reload on: below $10, add $40.
5. **Settings → Workspaces → Create Workspace** named `Sourcing Agent`. Open it → **Limits** → **Change Limit** → **$40/month**. Add email alerts at **$20** and **$32**, going to the shared mailbox.
6. **From inside that workspace**: API Keys → Create Key, named `sourcing-agent-production`. Copy it immediately; it is shown once.

> **Step 5 before step 6, always.** A spend limit applies only to the workspace it is set on. A key created from the organisation-level API keys page is not covered by it — the cap simply does not apply, and nothing tells you. If you create the key first and the workspace after, reissue the key from inside the workspace.

> **The key works before the client pays, but does nothing.** You can set the workspace limit and issue the key with no payment method on the account. Every call then fails for want of credit. So the key being present in Section 6.1 is not evidence the AI path works — Section 7.3 is the first point where that is actually proven, and it needs the client's step 4 done.

> **$40 against a ~$68 forecast.** The deployment plan forecasts ~$68/month at full volume. $40 is deliberately below that: the schedule is paused, real volume is unknown, and a hard ceiling while it is being learned is worth more than uninterrupted throughput. Two ways out once there are real numbers — raise the cap to ~$80, or enable batch processing, which halves AI cost to ~$34 and fits inside $40. The alerts at $20 and $32 arrive well before anything stops.

> **Where the money actually goes.** Three AI paths with very different costs: extraction (~$0.15 per tender, the bulk of it), supplier web search (~$0.10 per use), contact lookup (under a cent). An unexpected spike is almost always someone exploring the supplier search on the Review tab.

**Record:** `ANTHROPIC_API_KEY = sk-ant-…` — straight into the password manager. It is shown once, and reissuing it means updating it in three places (GitHub secret, Railway variable, your `.env`).

---

## 2. GitHub — code and the scheduled job

### 2.1 Create the organisation

Sign up on the mailbox from Section 0, then create an **organisation** rather than leaving the code in a personal account. A personal account belongs to one human; an organisation belongs to the company, and people are added and removed from it as roles change. Free tier is fine, and the client does not need to do anything here.

- Organisation name: agreed with the client beforehand
- Add yourself as an **Owner** under your own GitHub login, so you keep working after the client changes the shared-mailbox password at handover

> **That second point matters more than it looks.** Everything else you build is reachable through the shared mailbox, which stops being yours at handover. Your personal Owner seat on the org is what lets you keep supporting the repo afterwards without holding the client's credentials. Agree with the client whether you keep it.

### 2.2 Create the repository

Create an **empty private** repo named `sourcing-agent`. No README, no `.gitignore`, no licence — the push in the next step carries everything.

### 2.3 Push the code with its full history

From your existing clone:

```bash
cd /Users/noelkirthiraj/RADGlobal/sourcing-agent
git checkout main
git pull origin main                    # make sure you have everything

# Add the client repo as a second remote, leaving `origin` untouched for now
git remote add client https://github.com/<CLIENT-ORG>/sourcing-agent.git

git push client --all                   # every branch
git push client --tags                  # every tag
```

> **Why history and not a fresh commit.** Every "why is it like this" answer lives in the commit messages — why the scraper uses Playwright, why the cron is paused, why `ANTHROPIC_MAX_TOKENS` is 16384. A squashed handover throws away the reasoning and the next person re-learns it by breaking things.

Verify on github.com that the commit count matches. Then make `main` the default branch in **Settings → General**.

### 2.4 Branch protection (recommended)

**Settings → Rules → Rulesets → New branch ruleset** on `main`:

- Require a pull request before merging
- Require the `test` status check to pass

The CI runs 618 unit tests in under a minute. Making it a gate costs nothing and is the only thing standing between a bad afternoon and a bad week.

---

## 3. Railway — API and database

Railway hosts two things that are easy to confuse: the **API process** and the **PostgreSQL database**. Create the database first, because the API needs its connection string.

### 3.1 Account

1. **railway.app** → sign up, choosing **"Continue with GitHub"** on the client organisation account from Section 2. That makes connecting the repo in 3.3 one click.
2. The account starts on the **free trial**, which is fine for now — you can provision the database and deploy the API on it.
3. **Send the client `CLIENT_BILLING_SETUP.md` now**, with the logins for all four accounts through your password manager's secure-share. This is the right moment: every account exists, and Railway — the urgent one — is sitting on a trial clock.

> **Do not reach Section 4 on a trial account.** The trial expires and takes the database with it. Provisioning an empty database on a trial costs nothing if it lapses; migrating the client's entire tender and vendor history onto one and then having it expire is unrecoverable. Confirm the upgrade on the plan page — not from a verbal yes — before you run `pg_restore`.

### 3.2 Create the project and the database

1. **New Project** → name it `sourcing-agent`.
2. Inside the project: **New → Database → Add PostgreSQL**.
3. Wait for it to provision, then open the Postgres service → **Variables** → copy `DATABASE_URL`.

**Record:** `NEW_DATABASE_URL = postgresql://…`

### 3.3 Create the API service

1. In the same project: **New → GitHub Repo** → authorise Railway against the **client's GitHub org** → pick `sourcing-agent`.
2. Railway reads `railway.json` from the repo and configures itself:
   - Builder: Nixpacks
   - Start command: `python api.py`
   - Healthcheck: `/api/health`
   - Python version from `runtime.txt` (3.14)

   You should not have to set any of this by hand. If Railway asks, those are the values.

3. **Variables** on the API service — add these two:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` — use Railway's reference syntax, not a pasted literal, so it survives a database rotation |
   | `ANTHROPIC_API_KEY` | the key from Section 1 |

   Do **not** set `PORT`. Railway injects it and `api.py` reads it.

4. **Settings → Networking → Generate Domain.** Copy the result.

**Record:** `NEW_API_URL = https://<something>.up.railway.app`

### 3.4 Confirm it is alive

```bash
curl https://<NEW_API_URL>/api/health
# {"status": "ok"}
```

A healthy response here means Python started and the schema initialised. `db.init_schema()` runs on boot and is idempotent `CREATE TABLE IF NOT EXISTS`, so the new database now has all eight tables — **empty**. Filling them is the next section.

---

## 4. Database migration — the part to be careful about

This moves live operational data. Do it when nobody is using the dashboard.

### 4.1 Freeze writes

- Confirm the GitHub cron is still paused (it is — see `daily_agent.yml`), so no scrape can write mid-copy
- Tell anyone using the dashboard to stop for the next 30 minutes

### 4.2 Take a dump from the old database

Get the old `DATABASE_URL` from the **old** Railway project → Postgres → Variables.

```bash
cd ~/Desktop     # somewhere you will notice it

pg_dump "<OLD_DATABASE_URL>" \
  --no-owner --no-acl --format=custom \
  --file=sourcing-agent-$(date +%Y%m%d).dump
```

`--no-owner --no-acl` matters: the role names differ between Railway projects, and without these the restore fails on permissions it cannot grant.

**Check the file is real before continuing:**

```bash
ls -lh sourcing-agent-*.dump      # should be hundreds of KB at least, not 0
pg_restore --list sourcing-agent-*.dump | grep "TABLE DATA" | wc -l
```

That count should be **8** or close to it (`tenders`, `associates`, `vendors`, `products_by_vendor`, `rfp_categories`, `pos`, `tender_vendor_suggestions`, `tender_outreach`). If it is 0, the dump failed silently — stop and fix it rather than restoring an empty file over nothing.

### 4.3 Record what you expect to find afterwards

```bash
psql "<OLD_DATABASE_URL>" -c "
SELECT 'tenders' t, count(*) FROM tenders
UNION ALL SELECT 'vendors', count(*) FROM vendors
UNION ALL SELECT 'associates', count(*) FROM associates
UNION ALL SELECT 'pos', count(*) FROM pos
UNION ALL SELECT 'products_by_vendor', count(*) FROM products_by_vendor;"
```

**Write these numbers down.** They are how you know the restore worked.

### 4.4 Restore into the new database

The new database already has empty tables from `init_schema()`. `--clean --if-exists` drops them first so the restore owns the schema outright.

```bash
pg_restore --no-owner --no-acl --clean --if-exists \
  --dbname="<NEW_DATABASE_URL>" \
  sourcing-agent-*.dump
```

> Some warnings are normal and safe to ignore — typically `DROP ... does not exist` on the first pass. What is **not** safe to ignore is anything saying `relation already exists` combined with zero rows restored, or any `FATAL` line.

### 4.5 Verify before moving on

```bash
psql "<NEW_DATABASE_URL>" -c "
SELECT 'tenders' t, count(*) FROM tenders
UNION ALL SELECT 'vendors', count(*) FROM vendors
UNION ALL SELECT 'associates', count(*) FROM associates
UNION ALL SELECT 'pos', count(*) FROM pos
UNION ALL SELECT 'products_by_vendor', count(*) FROM products_by_vendor;"
```

**Every number must match Section 4.3.** If any differs, do not continue — re-run the restore. The old database is still intact, so there is no rush and nothing is lost.

Then restart the Railway API service (**Deployments → Redeploy**) so it reconnects, and:

```bash
curl https://<NEW_API_URL>/api/tenders/count
```

A plausible count rather than `0` means the API is reading real migrated data.

---

## 5. Vercel — the dashboard

### 5.1 Account

**vercel.com** → sign up with **"Continue with GitHub"** on the client organisation. Hobby tier is free and permanent — the dashboard is static HTML and JSON, no server and no build step — so there is nothing for the client to do here.

### 5.2 Point the dashboard at the new API — code change

Three files hardcode the old Railway URL, and one hardcodes the GitHub owner. **Do this before the first Vercel deploy**, or the dashboard will come up talking to RAD Global's API.

| File | Line | Change |
|---|---|---|
| `index.html` | 3068 | `const REVIEW_API = '<NEW_API_URL>';` |
| `index.html` | 2492 | `const GH_OWNER = '<CLIENT-ORG>';` |
| `review.html` | 234 | replace the fallback URL |
| `workload.html` | 112 | replace the fallback URL |

```bash
cd /Users/noelkirthiraj/RADGlobal/sourcing-agent
git checkout -b chore/point-at-client-infra

# Replace every occurrence of the old API host in one pass
grep -rl 'api-production-4195.up.railway.app' index.html review.html workload.html \
  | xargs sed -i '' 's|https://api-production-4195\.up\.railway\.app|<NEW_API_URL>|g'

# And the GitHub owner the "trigger run" button calls
sed -i '' "s|const GH_OWNER = 'NoelKirthiraj'|const GH_OWNER = '<CLIENT-ORG>'|" index.html

# Confirm nothing was missed
grep -rn 'api-production-4195\|NoelKirthiraj' index.html review.html workload.html || echo "clean"

git commit -am "chore: point dashboard at client Railway API and GitHub org"
git push client chore/point-at-client-infra
```

Open the PR on the **client** repo and merge it there. `po.html` and `vendors.html` need no change — they read the API base injected by the parent frame.

### 5.3 Deploy

1. **Add New → Project** → import `sourcing-agent` from the client GitHub org
2. Framework preset: **Other**
3. Build command: **leave empty**. Output directory: **leave empty**. It is a static site; `vercel.json` supplies the cache headers.
4. Deploy

**Record:** `NEW_DASHBOARD_URL = https://<project>.vercel.app`

### 5.4 Custom domain (optional)

If the client wants `sourcing.theirdomain.com`: **Settings → Domains → Add**, then create the CNAME Vercel shows you at the client's DNS provider. Propagation is usually minutes. Do this after everything else works — a domain on a broken deploy helps nobody.

---

## 6. Rewire the scheduled job

### 6.1 Secrets on the client repo

**Settings → Secrets and variables → Actions → New repository secret.** Four are in use today:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from Section 1 |
| `DATABASE_URL` | `NEW_DATABASE_URL` from Section 3.2 |
| `SAP_USERNAME` | unchanged — the client's existing SAP Ariba login |
| `SAP_PASSWORD` | unchanged |

> **About the CFlow secrets.** `daily_agent.yml` also references `CFLOW_API_KEY`, `CFLOW_BASE_URL`, `CFLOW_USER_KEY`, `CFLOW_USERNAME` and `CFLOW_WORKFLOW_NAME`. **None of them are set on the current repo** — CFlow submission is not wired up today, and the agent runs in database mode without it. Leave them unset unless and until CFlow integration is turned on. Do not invent placeholder values; `config.py` fails fast on a blank required variable, which is better than submitting to the wrong workflow.

### 6.2 Check Actions is enabled

**Settings → Actions → General** — new organisations sometimes default to disabled. Confirm:

- Actions permissions: **Allow all actions**
- Workflow permissions: **Read and write** — the job commits `data/*.json` back to the repo after each run (`daily_agent.yml:145-147`), and fails at the final step without write access

### 6.3 A new PAT for the dashboard's trigger button

The "Trigger run" button calls the GitHub API from the browser using a token the user pastes; it is stored in `localStorage` and is never committed.

1. On the **client** GitHub account: **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Repository access: only `sourcing-agent`
3. Permissions: **Actions: Read and write**, **Contents: Read-only**
4. Expiry: 90 days (calendar a reminder — the button silently 404s when it lapses)
5. On the new dashboard, paste it when prompted

### 6.4 Leave the cron paused

`daily_agent.yml` has its `schedule:` block commented out, and that is still correct. It stays paused until one manual run confirms SAP logs in — now doubly so, since this is also the first run on new infrastructure. Section 7.3 is that run.

---

## 7. Your local machine — and everything you do from here on

This is the step that answers *"from then onwards, whatever changes I make should point to the new repository."*

### 7.1 Repoint `origin`

```bash
cd /Users/noelkirthiraj/RADGlobal/sourcing-agent

git remote -v                           # origin → NoelKirthiraj/sourcing-agent

git remote rename origin radglobal-old  # keep it, do not delete it yet
git remote rename client origin         # the client repo becomes the default

git remote set-head origin -a
git branch --set-upstream-to=origin/main main

git remote -v                           # origin → <CLIENT-ORG>/sourcing-agent
```

From here, a bare `git push` goes to the client repo, which deploys to the client's Railway and Vercel. Nothing else in your workflow changes.

After two weeks of the new setup running cleanly: `git remote remove radglobal-old`.

### 7.2 Update `.env` for local runs

Your local `.env` still points at the old database. Open it and replace:

```
DATABASE_URL=<NEW_DATABASE_URL>
ANTHROPIC_API_KEY=<new key from Section 1>
```

`.env` is gitignored and never leaves your machine. Then:

```bash
source .venv/bin/activate
pytest tests/unit/ -q                   # 618 passed — no network, proves nothing broke
python run.py --dry-run --limit 3       # hits the portal, writes nothing
```

### 7.3 The first real run on new infrastructure

On the **client** repo: **Actions → Daily Sourcing Agent → Run workflow** → mode `daily`, limit `5`.

Watch for, in order:

1. The job starts at all — if not, revisit 6.2
2. SAP login succeeds — this is the open question from before the migration, now answered on new infrastructure
3. Tenders appear in the dashboard's Review tab
4. The run commits `data/*.json` back to the repo

If SAP fails twice, the halt guardrail trips and the dashboard shows a banner. Download the `sap-diagnostics-<run_id>` artifact — it has the page URL, title, body text and a screenshot from the moment of failure. Clear the halt with `python tools/clear_sap_halt.py` **after** fixing the cause.

### 7.4 Re-enable the schedule

Only once 7.3 has passed cleanly. Uncomment the `schedule:` block in `.github/workflows/daily_agent.yml` — the agreed times are already in there:

| Cron (UTC) | Days | Mode |
|---|---|---|
| `0 12 * * 1-5` | Mon–Fri | daily |
| `0 12 * * 6` | Saturday | weekly |

`12:00 UTC` is **8:00 AM ET in summer, 7:00 AM ET in winter** — GitHub cron does not follow daylight saving. Mention this to the client before they notice it in November.

---

## 8. Acceptance checklist

Work down it. Every line is a thing that has broken for somebody before.

**Infrastructure**
- [ ] `curl <NEW_API_URL>/api/health` → `{"status": "ok"}`
- [ ] `curl <NEW_API_URL>/api/tenders/count` → matches the old count from 4.3
- [ ] Row counts in the new database match Section 4.3 exactly
- [ ] Railway is on the **Hobby plan**, not an expiring trial — verified on the plan page **before** the data migration in Section 4
- [ ] Anthropic: `Sourcing Agent` workspace capped at **$40/month**, key issued **from inside it**, alerts at $20/$32 reaching the shared mailbox
- [ ] Anthropic: client has added a card and bought $40 of credit — confirm on the Billing page, not by asking
- [ ] All four accounts are registered to the migration mailbox, not a RAD Global address
- [ ] Two-factor is still **off** everywhere, for the client to enable at handover

**Dashboard** — open `NEW_DASHBOARD_URL` and confirm each tab
- [ ] Mission Control shows real run history, not sample data
- [ ] Review tab lists tenders; open one, assign an associate, confirm it persists after a refresh
- [ ] Vendors tab lists the full registry; open a vendor, scroll the edit drawer to the bottom, save a change
- [ ] Generate PO tab loads (no need to generate one yet)
- [ ] Browser devtools → Network: every `/api/` call goes to the **new** Railway host
- [ ] No console errors referring to the old host

**Pipeline**
- [ ] Manual `workflow_dispatch` run completes
- [ ] SAP login succeeds
- [ ] `data/*.json` is committed back to the client repo by the run
- [ ] "Trigger run" button works with the new PAT

**Handover**
- [ ] Every credential handed to the client through a password manager, not email or chat
- [ ] Client has worked through `CLIENT_BILLING_SETUP.md` Section 4: mailbox password changed, **recovery phone and recovery email replaced with theirs**, passwords changed on all four service accounts, two-factor enabled on their own devices
- [ ] Agreed in writing what access you keep afterwards — typically a personal Owner seat on GitHub and Railway for support, and none on Anthropic billing
- [ ] If any account is still registered to a RAD Global address, that is written down as outstanding with a date to fix it
- [ ] Your local `origin` points at the client repo (`git remote -v`)
- [ ] A test commit pushed from your machine deploys to the client's Vercel and Railway

---

## 9. Decommission — not before two weeks

Once the new system has run a full week of scheduled jobs without intervention:

1. **Take a final dump of the old database** and archive it somewhere durable. Cheap insurance.
2. Old Railway project → **Settings → Delete**. This stops the $5/month.
3. Old Vercel project → delete, or leave it (free, and a useful rollback for a while).
4. Old GitHub repo → **archive**, do not delete. It is the historical record, and archiving is reversible.
5. Old Anthropic key → **revoke**. Do this last and deliberately: anything still using it fails instantly, which is exactly how you find out whether anything still was.

---

## 10. Things that will go wrong, and what they mean

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard loads, every tab empty, console shows CORS or failed fetch | `REVIEW_API` still points at the old host | Section 5.2 — check all three files, not just `index.html` |
| `/api/health` fine, `/api/tenders/count` is 0 | Restore did not land | Section 4.4; verify counts before blaming the API |
| `pg_restore` errors on roles or ownership | Dumped without `--no-owner --no-acl` | Re-dump with both flags |
| Workflow fails at the final commit step | Workflow permissions are read-only | Section 6.2 |
| "Trigger run" button does nothing / 404 | PAT missing, expired, or wrong `GH_OWNER` | Sections 5.2 and 6.3 |
| AI extraction fails with a credit message | Client has not done `CLIENT_BILLING_SETUP.md` Section 1 yet, credit exhausted, or the $40 workspace cap is hit | Check the Anthropic Billing page. The code reports billing failures as billing, not as a bad PDF. |
| Everything worked, then the database vanished | Railway was left on the trial and it expired | Unrecoverable. This is why Section 3.1 says verify the Hobby upgrade before Section 4. |
| Client cannot sign in after handover | Two-factor was enabled on your device during setup | Disable it from your device, have them re-enable on theirs (§0) |
| Client changed every password but Google still texts *you* on a reset | The mailbox recovery phone was never replaced | §0 Option A, handover step 2 — this is the loose end that outlives the migration |
| SAP login fails twice then stops trying | Halt guardrail, working as designed | Read the diagnostics artifact first, then `tools/clear_sap_halt.py` |
| Railway deploy succeeds, service unreachable | No public domain generated | Section 3.3 step 4 |

---

## Appendix — credential record

Fill in as you go. **This sheet belongs in a password manager, not in the repo and not in email.**

| Item | Value | Where it is used |
|---|---|---|
| Mailbox the accounts are registered to | | All four account logins — and their recovery |
| Mailbox password | | Handed to the client at handover |
| Recovery phone on the mailbox (Option A) | | **Must be replaced by the client** — see Section 0 |
| Anthropic API key | | GitHub secret, Railway variable, your local `.env` |
| Anthropic workspace cap | $40 /month (workspace `Sourcing Agent`) | |
| New `DATABASE_URL` | | GitHub secret, Railway variable, your local `.env` |
| New API URL | | `index.html`, `review.html`, `workload.html` |
| New dashboard URL | | Shared with the client's team |
| Client GitHub org | | `GH_OWNER` in `index.html` |
| Dashboard trigger PAT | | Browser `localStorage`, expires ______ |
| SAP Ariba username | | GitHub secret |
| SAP Ariba password | | GitHub secret |
