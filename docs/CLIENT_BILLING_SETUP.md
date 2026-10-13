# Account & Billing Setup — Client Instructions

**For:** the client team member setting up accounts
**Prepared:** 13 October 2026
**Time required:** about 45 minutes, plus waiting on verification emails

---

## What you are setting up

Four online services run the Sourcing Agent. You create the accounts and add billing; RAD Global does the technical configuration afterwards.

| # | Service | What it does | Monthly cost |
|---|---|---|---|
| 1 | **Anthropic** | Reads tender documents and supplier quotes | **Capped at $40** |
| 2 | **Railway** | Runs the system and stores all data | **$5** |
| 3 | **Vercel** | Hosts the dashboard your team opens | **Free** |
| 4 | **GitHub** | Stores the software and runs the daily job | **Free** |
| | | **Total** | **$45 / month maximum** |

All prices are **USD**. Only Anthropic varies with use, and it is capped — see Section 1.

### Before you begin

- [ ] A company credit card
- [ ] A shared company email address for the accounts — for example `procurement-tech@yourcompany.com`
- [ ] 45 minutes

> **Please use a shared mailbox, not a personal one.** These accounts hold billing and production access. When they are tied to one person's address, that person leaving becomes an outage and a password-reset problem. A shared mailbox that two or three people can reach avoids both.

**Create the accounts in the order below.** Anthropic is first because new organisations occasionally need a short manual review before the account is fully usable, and that clock can run while you do the rest.

---

## 1. Anthropic — the AI service (capped at $40/month)

This is the only service whose cost depends on how much work the system does. It is set up so it **cannot** exceed $40 in a month.

### 1.1 Create the account

1. Go to **console.anthropic.com**
2. Sign up with the shared company email
3. When asked for an organisation name, enter the **company's legal name**. This is what appears on invoices.

### 1.2 Add a payment method

**Settings → Billing → Payment methods → Add**

Anthropic works on **prepaid credits**: you buy credit, and usage draws it down. There is no invoice at month end and no way to accidentally run up a bill — you cannot spend credit you have not bought.

### 1.3 Buy the first $40 of credit

**Settings → Billing → Buy credits → $40**

### 1.4 Set the top-up — $40 at a time

**Settings → Billing → Auto-reload → turn on**

| Setting | Enter |
|---|---|
| When balance falls below | **$10** |
| Automatically add | **$40** |

This keeps the system running without anyone watching it. When the balance drops to $10, another $40 is purchased automatically.

> **Why $10 and not $0.** If credit runs out completely, document processing stops mid-run and the team sees failures. Topping up at $10 leaves a few days of buffer so the reload happens quietly in the background.

### 1.5 Set the $40 cap — this is the important one

Auto-reload on its own would keep topping up forever. The cap is what makes $40 a genuine ceiling, and it is set on a **Workspace**.

1. **Settings → Workspaces → Create Workspace**
2. Name it **`Sourcing Agent`**
3. Open the workspace → **Limits** tab → **Change Limit**
4. Set the monthly spend limit to **$40**
5. On the same tab, add **email notifications at $20 and $32** — these go to the shared mailbox and give you warning before anything stops

### 1.6 Create the key — inside that workspace

This step is easy to get wrong, and getting it wrong quietly removes the cap.

1. Go to the **`Sourcing Agent` workspace** you just created
2. **API Keys → Create Key**
3. Name it **`sourcing-agent-production`**
4. **Copy the key immediately** — the console will never show it again
5. Send it to RAD Global through your password manager's secure-share feature, **not by email or chat**

> **Why this matters.** A spend limit applies to the workspace it is set on. A key created in the default workspace is not covered by the `Sourcing Agent` limit, and would have no cap at all. Create the key from inside the workspace, not from the organisation-level API keys page.

### 1.7 What happens when the cap is reached

Requests stop being accepted for the rest of the month. In practice:

- Tender extraction pauses — the daily job still collects tenders, but documents are not read
- The dashboard, the review workflow, vendor management and everything already captured **keep working normally**
- The team sees a clear message saying the AI credit is exhausted, not a generic error

To resume: raise the workspace limit (same screen as 1.5). It takes effect immediately.

---

### A note on the $40 figure — please read

The cost forecast in the deployment plan estimates **about $68/month** once the system runs at full volume — roughly 400 tenders a month at $0.15 each, plus SAP navigation and purchase orders.

**So a $40 cap will be reached before month end at full volume.** That is a deliberate choice, not an oversight, and it is a reasonable one to start with:

- The daily schedule is currently **paused**, so actual usage today is near zero
- Volumes ramp up rather than arriving all at once
- A low cap for the first month or two tells you what real usage looks like, with a guaranteed ceiling while you find out

**Two ways forward once you have real numbers:**

| Option | Effect |
|---|---|
| Raise the cap to ~$80 | Covers full forecast volume with headroom |
| Enable batch processing | Cuts AI cost roughly in half, to ~$34/month — comfortably inside $40. Documents are processed within a few hours instead of immediately, which suits an overnight job. Ask RAD Global to turn this on. |

Either way, start at $40. The notifications at $20 and $32 will tell you well before anything stops.

---

## 2. Railway — the system and the database ($5/month)

This runs the software and holds all your data: tenders, vendors, assignments, purchase orders.

1. Go to **railway.app**
2. **Sign up with GitHub** if you have already done Section 4 — it saves a step later. Otherwise sign up with the shared email.
3. **Upgrade to the Hobby plan — $5/month.** Settings → Plans.
4. Add the company card

> **The upgrade is not optional.** The free tier expires after a trial period and **takes the database with it when it does**. This is the one place where not paying $5 loses data. Please complete this step rather than leaving it on the trial.

Billing is a flat $5/month. Usage above the included allowance would add cost, but this system runs far below it.

---

## 3. Vercel — the dashboard (free)

This hosts the web page your team opens. It is plain files, so the free tier covers it permanently.

1. Go to **vercel.com**
2. **Sign up with GitHub** (do Section 4 first if you have not)
3. Choose the **Hobby** plan — free
4. **No payment card required**

If the company later wants the dashboard on its own web address — `sourcing.yourcompany.com` rather than a Vercel address — that is still free. Tell RAD Global and they will set it up.

---

## 4. GitHub — the software and the daily job (free)

This stores the software and runs the scheduled job each morning.

1. Go to **github.com** and sign up with the shared email
2. Create an **Organisation** (not just a personal account): **Settings → Organisations → New organisation**
3. Choose the **Free** plan
4. Name it after the company
5. **No payment card required**

> **Why an organisation rather than a personal account.** A personal account belongs to one human. If they leave, the company's software sits in an account nobody can reach. An organisation is owned by the company, and people are added to and removed from it as roles change.

Invite RAD Global as an **Owner** so they can complete the technical setup.

---

## 5. Hand these to RAD Global

Once all four accounts exist, RAD Global needs the following. **Please share them through your password manager's secure-share feature — not by email, chat, or a document attachment.**

| # | Item | Where it came from |
|---|---|---|
| 1 | Anthropic API key | Section 1.6 |
| 2 | Owner invitation to the GitHub organisation | Section 4 |
| 3 | Confirmation that Railway is on the **paid Hobby plan** | Section 2 |
| 4 | Confirmation that the Vercel account exists | Section 3 |
| 5 | SAP Ariba username and password | Your existing SAP account |

> **On the SAP login.** It must be an account used **only** by this system. A login shared with a person caused a two-month outage previously, because SAP Ariba does not allow the same account to be signed in twice at once. If the account you are about to provide is also used by a team member, please create a separate one.

---

## 6. Setup checklist

Work down the list and tick as you go.

**Anthropic**
- [ ] Account created with the shared company email
- [ ] Organisation named with the company's legal name
- [ ] Payment method added
- [ ] $40 of credit purchased
- [ ] Auto-reload on: below **$10** → add **$40**
- [ ] Workspace **`Sourcing Agent`** created
- [ ] Workspace spend limit set to **$40/month**
- [ ] Email alerts set at **$20** and **$32**
- [ ] API key created **inside the workspace** and shared securely

**Railway**
- [ ] Account created
- [ ] **Upgraded to Hobby — $5/month** (not left on the trial)
- [ ] Payment method added

**Vercel**
- [ ] Account created, Hobby plan

**GitHub**
- [ ] Organisation created, Free plan
- [ ] RAD Global invited as Owner

**Handover**
- [ ] All five items from Section 5 shared through the password manager
- [ ] Credentials stored where more than one person can reach them

---

## 7. Common questions

**Can this bill us more than $45 in a month?**
No. Railway is a flat $5. Vercel and GitHub are free. Anthropic is capped at $40 by the workspace limit in 1.5, and uses prepaid credit, so there is no end-of-month invoice to be surprised by.

**What happens if we hit the $40 Anthropic cap?**
Document reading pauses for the rest of the month. The dashboard, the tender list, vendor management and everything already captured keep working. You will have had email warnings at $20 and $32.

**Can we pay annually or by invoice instead of card?**
Anthropic offers invoicing for larger commitments — contact their sales team. At $40/month it is not usually worth the paperwork. Railway is card only at the Hobby tier.

**Who can see these accounts?**
Only people you invite. Use the shared mailbox for account recovery and keep every credential in the company password manager.

**What if we want to stop using the system?**
Everything is owned by the company. Cancel Railway and the Anthropic credit stops being drawn down; GitHub and Vercel are free and can simply be left. The data can be exported from the database at any time.

---

## Record of what was set up

Fill this in and keep it with the company's other service records. **Credentials belong in the password manager, not on this sheet.**

| Item | Detail |
|---|---|
| Shared email used | |
| Anthropic organisation name | |
| Anthropic workspace name | `Sourcing Agent` |
| Anthropic monthly cap | $40 |
| Anthropic auto-reload | Below $10 → add $40 |
| GitHub organisation name | |
| Railway plan | Hobby — $5/month |
| Date completed | |
| Completed by | |
