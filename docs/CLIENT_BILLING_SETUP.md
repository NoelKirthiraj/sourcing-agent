# Billing Activation — Client Instructions

**For:** the client team member who attaches payment
**Prepared:** 13 October 2026
**Time required:** about 15 minutes

---

## What this is

RAD Global has already created the four online accounts the Sourcing Agent runs on, under your company's name, and has configured them. **Two of them need a company payment card attached before the system can operate.** That is what this document covers.

You will have received, separately and securely, the login details for each account — plus the email address they are all registered to, which you take ownership of in Section 4.

| # | Service | What it does | Your action | Cost |
|---|---|---|---|---|
| 1 | **Anthropic** | Reads tender documents and supplier quotes | **Add card, buy $40 credit, turn on auto-reload** | Capped at **$40/month** |
| 2 | **Railway** | Runs the system and stores all your data | **Add card, upgrade to Hobby** | **$5/month** |
| 3 | **Vercel** | Hosts the dashboard your team opens | Nothing — free tier | $0 |
| 4 | **GitHub** | Stores the software, runs the daily job | Nothing — free tier | $0 |
| | | | **Maximum total** | **$45/month** |

All prices are **USD**.

### Two deadlines worth knowing

These are not arbitrary — things break if they are missed.

| Action | Needs to be done by | What happens otherwise |
|---|---|---|
| **Railway card + Hobby upgrade** | **Before RAD Global migrates your data** | The free trial expires and **deletes the database with it**, including every tender and vendor record migrated into it |
| **Anthropic card + credit** | Before the first live run | Document reading fails. Everything else works, but no tender gets read. |

Railway is the urgent one. Please do Section 2 first if you are short of time.

---

## 1. Anthropic — add payment and credit

Anthropic reads the tender PDFs and supplier quotes. It is the only cost that varies with use, and RAD Global has **already capped it at $40/month** so it cannot run away.

Sign in at **console.anthropic.com** with the details provided.

### 1.1 Add a payment method

**Settings → Billing → Payment methods → Add**

### 1.2 Buy the first $40 of credit

**Settings → Billing → Buy credits → $40**

Anthropic works on **prepaid credits** — you buy credit and usage draws it down. There is no invoice at month end, and you cannot be billed for credit you have not bought.

### 1.3 Turn on auto-reload

**Settings → Billing → Auto-reload → turn on**

| Setting | Enter |
|---|---|
| When balance falls below | **$10** |
| Automatically add | **$40** |

This keeps the system running unattended. When the balance drops to $10, another $40 is purchased.

> **Why $10 and not $0.** If credit runs out entirely, document processing stops part-way through a run and your team sees failures. Reloading at $10 leaves a few days of buffer so it happens quietly instead.

### 1.4 What is already set up for you

You do not need to do these — RAD Global has done them:

- A workspace named **`Sourcing Agent`** with a **$40/month spend limit**
- Email alerts at **$20** and **$32** so you get warning before anything stops
- The API key, created inside that workspace so the limit applies to it

### 1.5 What happens if the $40 cap is reached

- Document reading pauses for the rest of the month
- The dashboard, tender list, vendor management and everything already captured **keep working normally**
- Your team sees a clear "AI credit exhausted" message, not a confusing error

To resume, raise the limit: **Settings → Workspaces → `Sourcing Agent` → Limits → Change Limit**. It takes effect immediately.

---

### A note on the $40 figure

RAD Global's forecast puts AI cost at roughly **$68/month once the system runs at full volume** — about 400 tenders a month.

**So $40 is deliberately below the expected steady-state cost.** That is a starting position, not an error:

- The daily schedule is currently paused, so real usage today is near zero
- Volume builds up rather than arriving at once
- A firm ceiling for the first month or two tells you what usage actually looks like, with no risk of a surprise

Once there are real numbers, there are two ways forward — RAD Global can advise:

| Option | Effect |
|---|---|
| Raise the cap to about **$80** | Covers full forecast volume with headroom |
| Enable **batch processing** | Halves AI cost to about **$34/month**, comfortably inside $40. Documents process within a few hours rather than instantly, which suits an overnight job. |

Either way, $40 is the right number to start on. The alerts at $20 and $32 give you warning well before anything stops.

---

## 2. Railway — add payment and upgrade

**Please do this one first.** Railway runs the system and holds all your data: tenders, vendors, assignments, purchase orders.

Sign in at **railway.app** with the details provided.

1. **Settings → Plans → upgrade to Hobby — $5/month**
2. Add the company payment card

> **The upgrade is not optional, and the timing matters.** The free tier expires after a trial period and **takes the database with it when it does**. If your historical tender and vendor data has already been migrated in by then, that data goes too. This is the one place in the whole system where not paying $5 loses something that cannot be recovered.

Billing is a flat $5/month. Usage above the included allowance would add cost, but this system runs well below it.

---

## 3. Vercel and GitHub — nothing to do

| Service | Why no action |
|---|---|
| **Vercel** | Hosts the dashboard. It is plain files with no server, so the free tier covers it permanently. No card needed. |
| **GitHub** | Stores the software and runs the daily job. The free allowance is far above what this uses. No card needed. |

If you later want the dashboard on your own web address — `sourcing.yourcompany.com` rather than a Vercel address — that is also free. Tell RAD Global.

---

## 4. Taking ownership — do this last

RAD Global created these accounts for you and still holds the credentials. Once billing is attached and the system is confirmed working, complete the handover in the order below.

> **Order matters.** RAD Global needs working access until the first live run has passed. Change things early and they will be locked out mid-task — two-factor especially — and you will both do this twice.

### 4.1 The email account first

All four services were registered to a single email address, which RAD Global created for this purpose. **That mailbox is the recovery route for everything else:** password resets, billing receipts and security alerts all arrive there. Securing it is what actually transfers ownership — the other four passwords are almost a formality by comparison.

Sign in to it with the details provided, then:

1. **Change the password**
2. **Replace the recovery phone number with one of yours** — Settings → Security → Recovery phone
3. **Replace the recovery email with one of yours** — same screen
4. **Turn on 2-Step Verification**, on a device your company controls

> **Step 2 is the one people skip.** Until the recovery phone is changed, a password reset can still be sent to RAD Global's number — so you would hold the password while they hold the recovery route. It takes a minute and it is the difference between owning the account and borrowing it.

### 4.2 Then the four service accounts

For each of Anthropic, Railway, Vercel and GitHub:

1. Change the password to one your company controls
2. Turn on two-factor authentication, on your own device
3. Store both in your company password manager

### 4.3 Agree what access RAD Global keeps

Most clients keep them as an invited user on GitHub and Railway so they can continue supporting the system, with no standing access to billing. Whatever you decide, write it down.

Once 4.1 and 4.2 are done, the system is yours in every sense that matters: the billing, the data, the software, and the recovery route all sit with your company.

> **If the email address is a `@gmail.com` one** rather than one on your own domain, that is deliberate — it let RAD Global set everything up without waiting on your IT team. It works indefinitely. If you would prefer it on your company domain later, each service can have its email address changed; ask RAD Global to walk through it.

---

## 5. Checklist

**Railway — do first**
- [ ] Signed in
- [ ] **Upgraded to Hobby, $5/month** — not left on the trial
- [ ] Payment card added
- [ ] Told RAD Global it is done, so data migration can proceed

**Anthropic**
- [ ] Signed in
- [ ] Payment method added
- [ ] **$40 of credit purchased**
- [ ] Auto-reload on: below **$10** → add **$40**
- [ ] Told RAD Global it is done, so the first live run can proceed

**After the system is confirmed working — Section 4, in this order**
- [ ] Email account: password changed
- [ ] Email account: **recovery phone replaced with yours**
- [ ] Email account: recovery email replaced with yours
- [ ] Email account: 2-Step Verification on, on your device
- [ ] Passwords changed on all four service accounts
- [ ] Two-factor enabled on all four service accounts
- [ ] Credentials stored where more than one person in the company can reach them
- [ ] Agreed in writing what access RAD Global retains

---

## 6. Common questions

**Can this bill us more than $45 in a month?**
No. Railway is a flat $5. Vercel and GitHub are free. Anthropic is capped at $40 by a spend limit already in place, and runs on prepaid credit — there is no end-of-month invoice to be surprised by.

**Why does RAD Global hold the credentials right now?**
They created the accounts so you did not have to, and they need working access to finish the technical setup and run the first live test. Section 4 is where that access becomes yours.

**What is the extra email account for?**
All four services are registered to it, so it receives password resets, billing receipts and security alerts. Having one address rather than four scattered personal ones means ownership transfers in a single step — which is Section 4.1.

**What if we want to stop using the system?**
Everything is owned by your company — the accounts, the software and the data. Cancel Railway and the Anthropic credit stops being used. GitHub and Vercel are free and can be left alone. The database can be exported at any time.

**Can we pay by invoice instead of card?**
Anthropic offers invoicing for larger commitments; at $40/month it is rarely worth the paperwork. Railway's Hobby tier is card only.

**Who can see these accounts?**
Only the people you invite, plus RAD Global until Section 4 is complete.

---

## Record

| Item | Detail |
|---|---|
| Email account the services are registered to | |
| Email password changed | Date: |
| Email recovery phone replaced | Date: |
| Railway upgraded to Hobby | Date: |
| Railway card added | Date: |
| Anthropic card added | Date: |
| Anthropic $40 credit purchased | Date: |
| Anthropic auto-reload enabled | Date: |
| Passwords changed (Section 4) | Date: |
| Two-factor enabled (Section 4) | Date: |
| Completed by | |
