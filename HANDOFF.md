# HANDOFF — Nestor Insight · Sprint 2 M4

> **Read this file AND `streamlit_app.py` before doing anything.**
> Files (git) are the source of truth; this document is background + decisions.
> Do **one milestone (or one feature) per session**. Commit & push before closing.
> **Update the "Milestone Status" section at the end of every session.**

---

## 1. Sprint goal

Reframe the homepage from an **aggregate dashboard** into a **targeted retrieval workspace**:
the user finds goal-relevant AI-market signals in **under 10 seconds**.

## 2. Hard constraints (do not violate)

- Edit **`streamlit_app.py` only.** Do NOT touch the database, the analyzer's core logic, or the main API surface.
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

## 5. Central state scaffold (already in place from M4.0)

```python
st.session_state.view = {
    "meta_view":    "top_signals",   # cross-cutting overlay, not a category
    "signal_types": [],              # single taxonomy = signal_type
    "sentiment":    None,
    "search_query": "",
    # inclusion via MIN_URGENCY / MIN_IMPORTANCE (OR), NOT a sum threshold
}
```
`apply_filters()` is the **single** filtering entry point. Every downstream feature (search, pills, lens chart, panels, cards) reads this one state object.

## 6. Milestone status & scope

- **M4.0 — Decisions & Setup** ✅ DONE
  Data audit + locked decisions + central state scaffold + filter bug fixed + verified locally (OR = 119/264 ✅, old sum≥14 = 92 confirming the bug ✅, New(24h) = 0 is the true current state ✅).

- **M4.1 — Filtering & data layer** (mostly folded into the M4.0 scaffold)
  Confirm the landing view shows the ranked high-value set (119), not all 264. Small.

- **M4.2 — Entry points**
  Focus Search ("What are you tracking?") = local keyword match over `title / one_line_summary / entities`; Quick View pills wired to state; define search↔pill rule (only one active narrowing at a time); **remove the global sentiment chart** here.

- **M4.3 — Signal Action Card**
  Re-lay-out results as action cards using existing fields (`signal_type, target_persona, urgency, why_it_matters, business_implication, suggested_action`); footer `source · published_date · Read full article`; urgency color reads "act now" (accent), not "danger/error".

- **M4.4 — Visual panels**
  Bar → donut **Signal Lens** (display-only in v1, **no clickable-sector** master–detail; pills do the switching); **Keyword Relevance** panel (local frequency over `entities / title / one_line_summary`); **Market Tone** scoped to current view.

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

1. Open a fresh session → first message: "Read `HANDOFF.md` and `streamlit_app.py`, then implement **[only this milestone/feature]**."
2. Build one milestone (split further if large).
3. `commit && push`.
4. Update Section 6 (mark done, note anything learned).
5. Close the session.

---
*Last updated: end of M4.0 (filter bug fixed & verified; pending push).*
