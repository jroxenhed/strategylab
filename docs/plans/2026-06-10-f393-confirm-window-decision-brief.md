# F393 Confirm-Window Decision Brief — premise p-495676dd

**Date:** 2026-06-10
**For:** John (your call — this brief presents options, never decides)
**Gates:** F393 (the whole confirm path of the Desk premise workbench) — `[gated: John signs off on the confirm-window methodology]`

---

## Plain-English setup (read this first)

We have a trading idea waiting for its one real test.

- **The idea (premise `p-495676dd`):** when an insider at a small or mid-size company (market value ≤ $10B) *sells* shares in a discretionary way (not a pre-scheduled 10b5-1 sale), that stock then **underperforms the market** over the next 1–3 months. Plain version: insider selling is bad news that the price keeps paying for over the following weeks.
- **What's already been done — the "explore" look:** we ran this on **2015–2020** data ("explore" = an in-sample look used to *generate or sharpen* a hypothesis; cheap, allowed to be done many times, never counts as proof). Result: at the 30-trading-day horizon the picked stocks underperformed the market by **−11.74 percentage points** (pp), on **n = 20** qualifying events, and that beat the design's detection floor. Looks strong.
- **Why we can't just believe it:** the *direction* of the idea (expect a negative number) was itself read off the 2015–2020 data first (in the F396 census). So testing it again on 2015–2020 is **circular** — you can't discover a pattern in a dataset and then "prove" it on the same dataset. To call it a real, tradeable edge ("CONFIRMED"), it must survive a **single, one-shot test on data it has never touched** — the "confirm" window. That confirm test writes a permanent entry in the alpha-accounting ledger (`fdr_ledger.json`) and can only be spent **once per frozen design**. Which data window that test runs on, and the rules around it, is what you're deciding here.

**Terms of art used below, all defined inline at first use:**
- **MDE** = Minimum Detectable Effect: the smallest true edge (in pp) a test could reliably catch given its sample size. Smaller MDE = sharper test. We require the test be sharp enough to see the edge the design says it's hunting (the **design MDE = 8pp** for this premise). The one-sample MDE formula used throughout: `MDE = 2.80158 × std / √n`, with `std ≈ 10.5pp` (the spread of per-event excess returns, measured in explore).
- **n** = number of qualifying events (insider-sell filings that pass all the price/volume/market-cap floors and de-duplication).
- **Sealed grading** = the agent that grades the confirm result is a *fresh* one that has never seen the explore outcome — so it can't (even unconsciously) tilt the call.
- **Era / sub-era breakdown** = reporting the result split by calendar slices (e.g. 2021–22 vs 2023–24) so a single lucky year can't carry the whole verdict.
- **Multiplicity / FDR** = when you run many tests, some "win" by chance; False Discovery Rate accounting (Benjamini–Hochberg, "BH") corrects for that so you don't bank flukes.

---

## ⚠️ The fact that reshapes every option: the data cache stops in 2023

Before extrapolating event counts, I verified what years the **stratified Form-4 cache** (`backend/data/turnaround/edgar_cache/form4_stratified/`) actually covers. This is the only insider-filing source this premise's harness reads.

- Cohort date-keys in the cache index span **2015 → 2023 only** (seed 42, built 2026-06-05 for the *old* quarterly pond program — 569 cohort entries).
- **Consequence:** a confirm window written as "2021–2024" can today only draw events through **end-2023**. The nominal 4th year (2024) and anything in 2025–2026 **does not exist in the cache** and would require a fresh, larger Form-4 ingest before any of those windows are real. Treat 2024+ event counts below as *contingent on a cache rebuild*, not available now.

This single fact is why most options come out **underpowered** — see the table.

---

## Expected-n / MDE table (the core of the decision)

Scaling from explore: **20 valid events over 6 years (2015–2020) = ~3.33 qualifying events/year.** One-sample MDE = `2.80158 × 10.5 / √n`. Design MDE to clear = **8.0pp**. Reference: you need **n ≥ 14** to reach 8pp, and **n ≥ 20** to match explore's 6.59pp sharpness.

| Option (confirm window) | Years of real data | Est. n | One-sample MDE | Powered vs 8pp design? | Data available now? |
|---|---|---|---|---|---|
| **(a) 2021–2024 nominal** | effectively **2021–2023** (cache stops 2023) → ~3 yr | **~10** | **9.30pp** | ❌ underpowered | partial (2024 missing) |
| (a) *if* cache extended to full 2021–2024 | 4 yr | ~13 | 8.06pp | ❌ just misses | no — needs rebuild |
| **(b) 2021–2026H1** | ~5.5 yr | **~18** | **6.87pp** | ✅ powered (and ~explore-sharp) | no — needs big rebuild, **and burns all reserve** |
| **(c) 2021–2024 confirm + 2025+ reserve** | confirm leg = 2021–2023 (~3 yr) | **~10** | **9.30pp** | ❌ underpowered | partial |

**Headline:** at this premise's event frequency (rare — only ~3 qualifying insider-sells/year survive the small-cap + discretionary + floor filters), **no window short of ~5 years is even powered to see the 8pp design effect.** Only option (b) clears, and only by spending the entire future reserve on a single rare-event premise — exactly the trade-off F369 warned about (low-frequency premises are ~3× harder to power than high-frequency filing streams).

---

## Decision 1 — Which confirm window

### Option (a): 2021–2024 (4 calendar years)
- **What it buys:** the "classic" held-out window; matches the F339/momentum-test precedent that used 2021–2024 as the confirm era.
- **The contamination question (you flagged this):** F339 already **unsealed** 2021–2024 once — but only for the **D2-reversal / distress-recovery** family (beaten-down-but-still-filing stocks, +11.21% @126d, the read that spawned the gated F345 charter). F345's own caution line states that family **"cannot be graded on 2021–2024 again."** *That look was at distress-recovery longs, an entirely different signal from discretionary insider-selling shorts.* So for **p-495676dd specifically, 2021–2024 is NOT pre-contaminated** — nobody has peeked at the insider-sell excess in that window. The reuse restriction binds the **D2 family only**, not this premise.
  - *Caveat worth stating in the charter:* F339 globally established that 2021–2024 base rates and regime mix differ from 2015–2020 (the explore→confirm base rate shifted 0.524→0.380). That's a *known property of the era*, not a leak of this premise's outcome — but the confirm verdict should report it so a regime artifact isn't mistaken for the insider-selling edge.
- **The killer:** with the cache stopping in 2023, this is **effectively 2021–2023, n≈10, MDE 9.30pp > 8pp** → the test **cannot see the design effect.** Even with a cache rebuild to full 2024, n≈13 → 8.06pp, still a hair short. A failure here is ambiguous ("couldn't have seen it" vs "no edge"), which is the worst outcome the program explicitly tries to avoid (F340 lesson).
- **Recommendation:** **Do not confirm on (a) as-is.** It is underpowered and would burn the one-shot draw on a test structurally unable to deliver a clean CONFIRM or a clean KILL.

### Option (b): 2021–2026H1 (~5.5 years, all forward data)
- **What it buys:** the **only powered window** (n≈18, MDE 6.87pp ≤ 8pp). Most events, sharpest test, best shot at a real verdict.
- **The cost:** it consumes the entire forward reserve — **no untouched data is left** for a second look at this or any related insider-sell premise. It also requires the largest cache rebuild (Form-4 ingest extended through 2026H1). And it spans a very heterogeneous era (2021 meme-rally → 2022 bear → 2023–24 recovery → 2025+), so a single sub-era could dominate (mitigated by the mandatory era breakdown, Decision 4).
- **Recommendation:** **The defensible choice *if* you want a verdict on p-495676dd now and accept spending the reserve.** It's the honest "power-first" pick. But it is a one-way door: lose the reserve, and the program rule "gate hard only at confirm touches, keep a fresh window in hand" is gone for this premise family.

### Option (c): 2021–2024 confirm + 2025+ untouched reserve
- **What it buys:** keeps a genuinely-fresh 2025+ window sealed for a *second* independent look (a re-test, or a refined design), preserving the program's "always hold a reserve" discipline.
- **The flaw:** the confirm leg is still just 2021–2023 today (n≈10, MDE 9.30pp) — **underpowered**, same as (a). You'd be spending the one-shot draw on a weak test *and* fragmenting the data so the reserve is too small (2025+ alone ≈ 5 events) to ever be powered either. Splitting a rare-event premise across two windows guarantees **both** are underpowered.
- **Recommendation:** **Not for this premise.** Reserve-splitting is the right instinct for *high-frequency* premises (PEAD/8-K, thousands of events) where each half still clears power. For a ~3-events/year signal it just produces two tests that can't see anything.

### **Decision-1 recommendation (mine, for your call):**
**Neither confirm now on (a) nor split via (c).** The premise is **rare-event-underpowered** in every window the cache can supply today. Two clean paths:
1. **If you want to honour the design as frozen:** rebuild the Form-4 cache forward and confirm **once on (b) 2021–2026H1** — the only powered option — accepting the reserve is spent. Best shot at a real CONFIRM/KILL.
2. **If you'd rather not burn the reserve on an underpowered rare signal:** declare p-495676dd **UNTESTABLE-underpowered at confirm** (the R-1b precedent — same situation, same honest verdict), keep the explore read as a *measurement not a discovery*, and require a **structurally bigger net** before any confirm (relax floors / widen to a broader insider-event definition / a longer period), which is a new charter, your gate.

---

## Decision 2 — Multiplicity (how a confirm touch is paid)

- **One-shot per `spec_hash` (existing structure, keep it).** `graduate_to_confirm` freezes the design into a `spec_hash`, runs a power pre-check (`_check_power_audit`, programme sanity gate at 10pp), and does store-wide idempotency so the **same frozen design can never be confirmed twice.** F393 wires `awaiting_confirm → confirmed` by running the real OOS study **once** and appending **one** `fdr_ledger.json` entry for that hash.
- **On failure (your call to pin):**
  - *(Recommended)* **Premise dies on that design.** A confirm KILL is final for the frozen `spec_hash`. You may author a **new** premise (new hash) with a different design, but it must use a **fresh window** — never re-grade the spent one. This preserves one-shot integrity.
  - *(Rejected)* "Revisable with a fresh window only" without a new hash — invites quiet design-tweaking after seeing the result (the exact data-dredge the deflated-Sharpe tax exists to stop).
- **Explore→confirm family, BH together or alone?**
  - Explore looks are **FDR-ledgered as a family** (cheap, many). The **confirm touch stands alone** in its own draw — it is the scarce, irreversible spend. Per PROGRAM rule 1 + rule 6a ("multiplying bars multiplies false discoveries"): the confirm reports **ONE primary bar** (universe excess) with the four honesty lenses as *reporting-only*, and pays its BH correction **within the confirm family of hypotheses tested in that single touch** (here a BH-family of 1 — the 30d primary horizon — so the correction is trivial; the 10d/21d horizons are secondary lenses, not co-equal bars). Do **not** pool the confirm p-value into the explore BH family — that would let cheap explore looks dilute the one expensive test.
- **Recommendation:** keep one-shot-per-hash; **confirm KILL = premise dead on that design, new design needs a fresh window**; confirm stands alone with a BH-family-of-1 on the single primary horizon.

---

## Decision 3 — Sealed grading (the mechanical guarantee)

- **Rule:** PROGRAM rule 1 — confirm grading is done by a **fresh, outcome-blind agent**. The operator session that built/explored p-495676dd **cannot grade it** (it has seen the −11.74pp explore read and the era/regime breakdown).
- **Mechanical spec for F393:**
  1. The confirm run is launched as a **separate worker job** (via `worker-dispatch.sh`) whose grading step is handed **only**: (i) the **frozen charter / `spec_hash`** (the pre-registered design, no outcome fields), and (ii) the **confirm artifact** (the just-computed OOS numbers it must grade against the pre-stated bars). It must **not** receive the explore verdict JSON, the era/regime lens from explore, or the operator's chat context.
  2. Grading is **deterministic from the frozen charter** — pass/fail bars (direction, CI, era-agreement) are read from the charter, applied to the confirm artifact, emitting a verdict doc + the single ledger entry. No human/agent judgment can move a bar after the fact.
  3. **Append-only ledger write** under the existing file-lock (`fileutil.file_lock`), idempotent on `spec_hash`, with a pre-write backup (the `.pre-r1b.bak` pattern) and a clean-extension check before install (the F367/R-1b discipline).
- **Recommendation:** F393's confirm executor dispatches grading as a **distinct blind step** that sees charter + artifact only; assert in code that the explore-outcome paths are never passed in (mirror the existing `fdr_ledger_path=None` invariant style — a structural guarantee, not a convention).

---

## Decision 4 — Era / regime reporting inside the single touch

- **Rule 4:** the confirm verdict must report a **per-sub-era breakdown within the one touch** (John's walk-forward instinct), and explore sign-agreement across sub-eras gates whether confirm is even allowed.
- **What that means for a 3–4yr window:** split the confirm window into **2–3 contiguous sub-eras** (e.g. 2021–22 / 2023–24, or thirds for a 5.5yr window) and report the mean excess + n in each, **alongside** the single pooled primary bar. The pooled number stays the verdict; the sub-era table is a **fail-safe, not a second bar** — if the whole effect lives in one sub-era (e.g. only the 2022 bear), the verdict is flagged **era-carried** and capped, not promoted.
  - *Reality check for this premise:* explore's own era lens already shows **fading** — 2015–16 −14.1pp, 2017–18 −13.5pp, 2019–20 only −5.8pp. A confirm window must show the sign **holds** across its sub-eras, or the verdict is WEAKENED regardless of the pooled number. At n≈10–18 total, each sub-era cell is **3–6 events** — too thin to be load-bearing on its own, so the breakdown will mostly be a *consistency check* (do all cells share the negative sign?) rather than a per-era effect estimate. State that limitation in the verdict.
- **Recommendation:** report 2–3 sub-era cells (sign + n + mean), gate on **sign-agreement** (not per-cell significance, which the thin n won't support), flag era-carried if one cell dominates. Same for the regime lens (calm/neutral/stormy) — reporting-only, never load-bearing, crisis never counts.

---

## Decision 5 — What happens to p-495676dd under each option (worked example)

Starting point: explore read −11.74pp @30td, n=20, std 10.5pp, MDE 6.59pp, design MDE 8pp, **circular** (direction read off the same 2015–2020 data). Status today: `explored` (not yet graduated).

- **Under (a) 2021–2024:** `graduate_to_confirm` freezes the hash; the OOS run draws **~10 events (2021–2023 only — cache stops 2023)**; one-sample MDE ≈ **9.30pp > 8pp** → the harness self-reports **UNTESTABLE-underpowered**. Even if the point estimate is negative, the test couldn't have seen the design effect, so a "null" is uninformative and a "hit" is fragile. **One ledger draw spent for an ambiguous result.** *Not recommended.*
- **Under (b) 2021–2026H1:** after a cache rebuild, OOS draws **~18 events**, MDE ≈ **6.87pp ≤ 8pp** → **a real test.** Outcomes: if mean excess is clearly negative, BH-rejected, and the sign holds across 2–3 sub-eras → **CONFIRMED** (first confirmed Desk edge; peer lens must agree or it's sector-carried). If it's flat/positive or fails the CI → **KILL**, premise dead on this hash, and the **reserve is gone** (a second look needs a new design + new data). *This is the only path to a clean verdict now.*
- **Under (c) confirm 2021–2024 + 2025+ reserve:** confirm leg = **~10 events (2021–2023), MDE 9.30pp** → **underpowered**, same ambiguous result as (a) — *and* the held-back 2025+ reserve is ~5 events, far too thin to ever power a second look. **Both halves underpowered.** *Not recommended for a rare-event premise.*

**Plain-English bottom line for John:** this insider-selling idea is *rare* — only about three qualifying small-cap discretionary insider-sells per year survive the filters. The 2015–2020 explore showed a big −11.74pp underperformance, but (1) that read is circular (the direction was found in that same data), and (2) every confirm window we can build from today's cache has **too few events to give a trustworthy yes/no** — except the longest one (2021 through mid-2026), which needs a data rebuild and uses up *all* the future "fresh" data we've been saving. So the real fork is: **rebuild the data and take the one good shot on the long window (b)**, or **call it underpowered now (like R-1b), keep the reserve, and demand a wider net before any confirm.** Either is defensible; confirming on the short windows (a)/(c) is not — they spend the one irreversible test on a question they can't actually answer.

---

*Sources verified for this brief:* premise spec + explore verdict (`backend/data/premises.json`, `…/event_studies/premise_p-495676dd_explore_1781055085/s1_onesample_verdict.json`); cache year-span (`…/edgar_cache/form4_stratified/index.json`, 2015–2023); graduation gate (`backend/research/premise_run.py`); precedent (F339/F345 in JOURNAL.md + TODO.md, R-1b in PROGRAM.md); methodology rules (`docs/research/PROGRAM.md` rules 1/4/6a/6b).
