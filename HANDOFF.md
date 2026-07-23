# HANDOFF — Nestor Insight · Sprint 2 M4

> **Read this file AND `app/dashboard/streamlit_app.py` before doing anything.**
> Files (git) are the source of truth; this document is background + decisions.
> Do **one milestone (or one feature) per session**. Commit & push before closing.
> **Update the "Milestone Status" section at the end of every session.**

---

## 1. Sprint goal

Reframe the homepage from an **aggregate dashboard** into a **targeted retrieval workspace**:
the user finds goal-relevant AI-market signals in **under 10 seconds**.

## 2. Hard constraints (do not violate)

- Edit **`app/dashboard/streamlit_app.py` only.** Do NOT touch the database, the analyzer's core logic, or the main API surface.
- `plotly` is **optional**. If not used, fall back to Streamlit-native charts. Do not hard-depend on it.
- **No new backend fields.** Everything is built from data that already exists.

## 3. Data contract (confirmed by M4.0 audit — 264 analyzed articles)

**Available today (Sprint 3, all 10 fields 100% present — use directly):**
`signal_type, importance, sentiment, entities, one_line_summary, why_it_matters, business_implication, suggested_action, target_persona, urgency`

- `entities` is a **Python list** (company names already tagged → keyword matching is zero extra cost).
- `source` is a **single string** (e.g. "BBC News"). It is **NOT a count** — never display "N sources".
- High-priority density: **119 / 264 (45%)**.

**Genuinely missing / not available:**
- keyword relevance score → compute **locally** this sprint (Tier 0, no API).
- source **count** → does not exist (see above).
- saved watchlist persistence → out of scope for M4.
- click instrumentation → out of scope for M4.

## 4. Locked decisions (already encoded as constants)

- **Default filter (inclusion rule):** `urgency >= 7 OR importance >= 7` → **119/264**.
  - ⚠️ **DO NOT use `urgency + importance >= 14`.** That is a different, stricter rule (only **92** rows, and not equivalent to "both ≥ 7"). It was a bug; it has been fixed. Do not reintroduce it.
  - Implemented as `MIN_URGENCY = 7` / `MIN_IMPORTANCE = 7` + `is_high_priority()`; `META_VIEWS["top_signals"]` uses `"high_priority_or": True`; `apply_filters()` applies OR logic.
- **Ranking (sort key):** `priority_score = urgency + importance`, descending, equal weight, max 20. Used **only for sorting**, never as an inclusion gate.
- **Taxonomy:** `signal_type` is the **single** taxonomy. Meta-views (`top_signals`, `bd_opportunities`, …) are **cross-cutting overlays, not categories.** Do not build two divergent category schemes.
- **Vocabulary:** `urgency` = immediacy · `importance` = strategic weight · `priority_score` = sort key (max 20).
- **KPI cards:** "Avg Importance" **replaced** by "High Priority" (count via `is_high_priority()`); added "New (24h)" using **`published_at`** last-24h (NOT `analyzed_at`, which is 0 because the collector isn't scheduled); "Positive Tone" **scoped to the current view**.
- **Signal Action Card footer:** `source · published_date · Read full article`. **No source count.**
- **Sentiment shown once:** remove the global sentiment chart (do this in M4.2). Keep the tone KPI (scoped) + per-card sentiment.

## 5. Central state scaffold (in `app/dashboard/streamlit_app.py`)

```python
st.session_state.view = {
    "meta_view":     "top_signals",   # cross-cutting overlay, not a category
    "signal_types":  [],              # single taxonomy = signal_type
    "sentiment":     None,
    "search_query":  "",
    "min_urgency":   7,               # OR logic — either alone qualifies
    "min_importance": 7,
    "show_all":      False,           # True = bypass priority filter, show all 264
}
```
`apply_filters()` is the **single** filtering entry point. Every downstream feature (search, pills, lens chart, panels, cards) reads this one state object.

## 6. Milestone status & scope

- **M4.0 — Decisions & Setup** ✅ DONE
  Data audit + locked decisions + central state scaffold + filter bug fixed + verified locally (OR = 119/264 ✅, old sum≥14 = 92 confirming the bug ✅, New(24h) = 0 is the true current state ✅).

- **M4.1 — Filtering & data layer** ✅ DONE
  Default landing renders 119 high-priority signals (urgency≥7 OR importance≥7), sorted by priority_score desc. "View all signals" toggle expands to all 264. Verified locally (119 ✅, sort desc ✅, show_all=264 ✅). HANDOFF file-path refs updated to `app/dashboard/streamlit_app.py`.

- **M4.2 — Entry points** ✅ DONE
  Focus Search wired to `search_query` (matches title + one_line_summary + entities). Quick View pills: meta-views (Top Signals, BD Opps) set `meta_view`; signal-type pills (Regulatory Risk, Competitor Moves, Funding & Startups, Product Launches) set `signal_types`. Search↔pill mutual exclusion enforced in callbacks. Global sentiment chart removed. Verified: default=119 ✅, search narrows ✅, pills narrow correctly ✅, mutual exclusion ✅.

- **M4.3 — Signal Action Card** ✅ DONE
  Cards show: tags row (signal_type chip + persona chip + ⚑ High Priority), urgency badge top-right (amber 9-10 / blue 7-8 / slate 5-6, no red), title, one_line_summary, why + action always visible, business_implication in expander, footer `source · date · Read full article`. No "N sources" anywhere. Verified against 5 top-urgency cards.

- **Bugfix (post-M4.3): empty working set on deploy** ✅ FIXED
  Symptom: "No analyzed articles returned from API" banner; High Priority / New / Positive Tone = 0; stats KPI still 264. Root cause = client bug, NOT infra: `fetch_all_events` sent `limit=264`, but `/events` caps `limit` at 100 (`le=100`) → **HTTP 422** (dict, not list) → `isinstance` guard → empty set. `/events/stats` has no limit param so it stayed at 264 (explains the split). Fix (in `streamlit_app.py` only): paginate via the API's existing `offset` param in pages of `API_PAGE_SIZE=100`, stop on first short page; no server-side filter params. Non-200 or non-list now raises with `HTTP <status>` + first 200 chars so the banner is diagnosable. Verified against live API: 264 fetched, 0 dupes, High Priority=119.

- **M4.4 — Visual panels** ✅ DONE
  Signal Type bar → **Signal Lens donut** (Altair — plotly not installed, altair ships with Streamlit; display-only, no sector clicks) + **Keyword Relevance** ranked bar, side by side, **both scoped to `filtered_articles`** (update on pill/search). Donut collapses to ≤8 sectors (top 7 + "Other") with `SIGNAL_TYPE_LABELS` (no truncated enums). Keyword panel = entity frequency in view, ties broken by summed importance. Market Tone stays the single scoped "Positive Tone" KPI — no duplicate added. Helpers `signal_lens_data()` / `keyword_relevance()` are pure. Verified across default/pill/search: labels readable ✅, ≤8 sectors ✅, panels differ per view ✅, tone scoped (50.4% / 77.8% / 55.6%) ✅. Note: a single signal-type pill yields a 1-sector donut (expected, harmless).

- **M4.5 — Validation & polish**
  Functional check: selecting a **pill** updates results + keyword panel + tone. QA the 6 lenses; copy & affordance cleanup; capture before/after screenshots (portfolio asset).

## 7. Design images (original screenshot + render)

The render is the **north-star for look & feel only — aspirational, not a pixel spec.**
Where the render conflicts with Section 4, **the decisions win.** Known deltas to NOT reproduce:
- Signal Lens pie = **display-only v1** (pills switch, sector clicks do not).
- **One** taxonomy (render shows pills ≠ pie sectors — ignore that split).
- Market Tone **scoped to current view**, shown **once** (remove duplicate global sentiment).
- Card footer = `source · published_date · Read full article` — **no source count**.
- "Avg Importance" KPI is **replaced** (see Section 4).

## 8. Session workflow (repeat every time)

1. Open a fresh session → first message: "Read `HANDOFF.md` and `app/dashboard/streamlit_app.py`, then implement **[only this milestone/feature]**."
2. Build one milestone (split further if large).
3. `commit && push`.
4. Update Section 6 (mark done, note anything learned).
5. Close the session.

## 9. Sprint 5 data pipeline status

- **M5.1 — Persistent analysis state & content identity** ✅ DONE
  - Added `sql/sprint5_analysis_state.sql` and applied it to Supabase production.
  - New fields: `analysis_status`, `relevance_score`, `skip_reason`,
    `analysis_attempts`, `analysis_error`, `analysis_attempted_at`,
    `content_hash`.
  - Existing production audit after migration: 264 rows, all 264 marked
    `analyzed`, all 264 hashes present, zero SQL/Python hash mismatches.
  - Collector now generates a SHA-256 identity from normalized title + UTC
    publication date and checks it before insertion.
  - Analyzer reads the persistent status queue. Low-relevance, invalid, and
    duplicate rows become `skipped`; successful rows become `analyzed`;
    failures become `failed`.
  - `--retry-failed` retries failed rows. `--reprocess-skipped` deliberately
    rechecks skipped rows. Dry-run never persists state.
  - M4 dashboard constraints in Sections 2–7 remain locked for dashboard work;
    this M5.1 milestone intentionally changes the collector/analyzer only.

- **M5.2 — Resilient, observable RSS collection** ✅ DONE
  - Collector now uses `httpx` with a 15-second timeout, redirects, a named
    User-Agent, three attempts, and incremental retry backoff.
  - One HTTP client is reused across the run. Existing URLs and content hashes
    are queried in batches; new articles are written in batches with
    row-isolation fallback if a batch fails.
  - Added per-source status, fetched/new/duplicate/invalid counts, attempts,
    duration, and final run totals.
  - Added `--dry-run`, repeatable `--source`, and `--max-per-feed`.
  - Production acceptance: all 10 feeds healthy on first attempt; 192 current
    RSS entries inspected; 189 new `pending` rows inserted. Database after run:
    453 total = 264 analyzed + 189 pending, zero duplicate hashes and URLs.
  - A post-write dry-run classified 190/192 current entries as duplicates; two
    fresh The Verge entries appeared between runs, confirming the feed changed
    during acceptance rather than a database duplicate leak.

- **M5.3 — Controlled historical backfill** ✅ DONE
  - Added `app/collector/wordpress_backfill.py` for production historical
    discovery through the TechCrunch public WordPress archive.
  - Added `app/collector/historical_backfill.py` as an experimental secondary
    source through the GDELT DOC API.
  - TechCrunch works newest-to-oldest by archive page; GDELT uses bounded date
    windows and reports saturated windows. Both default to preview-only, stop
    at a target row count, retry transient failures and rate limits, and exit
    nonzero when a page/window or database write fails.
  - Historical candidates reuse the collector's canonical URL, content hash,
    batched database deduplication, and resilient write path.
  - Local title/summary relevance keeps broad archive results out of the paid
    analysis queue. Written rows remain `pending` until the analyzer runs.
  - GDELT live preview was blocked by a persistent shared-IP HTTP 429; no
    GDELT rows were written. The TechCrunch endpoint was healthy and reported
    5,142 matching archive rows for the default search.
  - Production acceptance used a 25-row preview and write pilot before scaling.
    The full run inspected 1,700 archive results across 17 pages and inserted
    the remaining 975 rows with zero failed pages or writes.
  - Final production audit: 1,453 total rows = 329 analyzed + 124 skipped +
    1,000 pending. The archive import has zero duplicate URLs, zero duplicate
    hashes, zero missing required fields, and zero missing summaries. Published
    dates span 2021-11-16 through 2026-07-22.
  - Analyzer dry-run sampled the first 25 pending rows: all 25 entered the
    analysis queue, with no Claude calls or database writes.

---
*Last updated: Sprint 5 M5.3 (1,000 historical articles imported and verified).*
