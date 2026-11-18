# Business case: Ribhiya for Egyptian small businesses

*Updated 2026-10-29 after testing on a real retail export. Figures marked **measured** come from that test
(`evals/real_data/`); everything else is an assumption to check with pilot customers.*

## Who pays, and for what

Egyptian product sellers (Instagram/WhatsApp shops, small brands, online stores) with roughly 100–2,000 orders a
month, usually with no accountant. Their problem: sales grow but profit doesn't, and they can't see where it leaks.
Ribhiya reads the exports they already have and gives three kinds of value:

| Value | How | Status |
|---|---|---|
| **Find the leak** | per-order profit, like-for-like price vs basket vs cost split, refunds, fees, packaging, shipping | measured: exact on 141k real lines; real causes found |
| **Test decisions first** | what-if simulator on the owner's real month (price, packaging, shipping, discounts, returns, free-shipping threshold, supplier quotes) | measured: correct, assumptions listed |
| **Win back revenue** | lapsed-customer list + WhatsApp drafts + bundles, with a comparison group that measures the real extra sales | built and backtested; real uplift still to be measured with pilots |
| **Save time** | replaces a monthly "where did my profit go" spreadsheet session | assumption: 4–10 h → under 1 h |

## Value per business (base case: 300 orders × 450 EGP = 135,000 EGP sales/month)

| | Low | Base | High |
|---|---|---|---|
| Hours saved / month | 3 | 6 | 9.5 |
| Time value (100–300 EGP/h) | 300 | 1,250 | 2,850 |
| Profit recovered (leak of 2–5% of sales, 25–50% fixed) | 675 | 1,418 | 3,375 |
| **Value / month (EGP)** | **975** | **2,668** | **6,225** |

Model: `evals/real_data/impact.py`. Revenue from win-back is **not** included above. It is counted only after the
comparison group proves it, because on the real data about half of "lapsed" customers returned without any message.

## What it costs to run (measured)

| Item | Per business / month |
|---|---|
| Hosting (one small VPS ≈ USD 6 serves 50–300 businesses; analysis is on demand, 0.2–2 s per page) | 1–7 EGP |
| AI answers: rules mode or free tier | 0 EGP |
| AI answers on a paid tier (≈2,900 input / 4,900 total tokens per question ≈ 1.2 EGP; 10–60 questions) | 12–73 EGP |
| WhatsApp win-back messages (copied by the owner) | 0 EGP |
| Supplier emails (capped at 20/day per business) | ≈0 EGP |

Everything except free-text questions works without AI, and each business must opt in before any AI is used.

## Pricing proposal (EGP, to validate with pilots)

Egyptian SMEs already pay about 800–1,600 EGP/month for business software (e.g. Wafeq accounting from 805 EGP,
YouCan stores from USD 29). Ribhiya should sit well below that so the decision is easy:

| Plan | Price / month | For | Includes |
|---|---|---|---|
| **مجاني Free** | 0 | trying it | 1 investigation, 2 months of data, all calculations, rule-based answers |
| **أساسي Starter** | 249 | one shop | unlimited months, what-if, Grow sales (win-back, bundles), AI answers (if enabled, 40 AI calls/month ≈ 40 direct or ~13 Hermes answers; then rule-based) |
| **نمو Growth** | 499 | growing brand | up to 3 shops/branches, supplier outreach emails, AI 120 calls/month, WhatsApp support |
| **محاسبين Accountant** | 1,499 | accountants/agencies | up to 10 client businesses, one login per client |

- Annual = 10× monthly (2 months free). Pay with InstaPay, Vodafone Cash, Fawry or card (Paymob).
- Gross margin: Starter 249 vs ≤55 EGP worst-case run cost (40 calls × ~1.2 EGP + ≤7 EGP hosting) → at least 78%;
  Growth 499 vs ≤155 EGP → at least 69% (typically about 95% on rules/free tier). The monthly cap is enforced in code
  (`TENANT_MONTHLY_LLM_CALL_CAP`, a Hermes answer counts as 3 calls); when it runs out, answers continue rule-based.
- Value-to-price: base case 2,668 EGP value for 249 EGP ≈ 10×; even the low case ≈ 4×.
- Trust offer: first month free. After 60 days the win-back comparison shows the owner the measured result on
  their own data.

## Go-to-market

1. **5–10 paid pilots** (free month, then Starter) with Instagram/WhatsApp shops and small brands, via incubators and
   seller communities. Measure: hours saved (owner-reported), contribution per order before/after (Ribhiya's
   experiment tracking), win-back uplift (comparison group).
2. **Accountants and bookkeepers** who serve many small shops: one relationship brings 10+ businesses.
3. **Platform partnerships** (store builders, couriers, payment gateways): an export → Ribhiya button.

## Readiness for paying customers

Done: real-data reliability fixes, per-business data isolation, AI consent per business, per-business AI and email
caps, erase-all-data, upload hardening, security headers and strict CSP (see `docs/SECURITY.md`).

Needed before charging at scale:
- user accounts and login (e.g. phone/WhatsApp OTP) in place of shared tokens;
- billing integration (Paymob/Fawry);
- an encrypted disk for the database, plus backups;
- a paid, no-training AI tier (or AI kept off) for businesses that enable it;
- a short privacy policy and terms in Arabic (Egypt PDPL 151/2020).
