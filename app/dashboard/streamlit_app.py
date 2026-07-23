"""
Nestor Insight — AI Industry Signal Monitoring Dashboard

M4.0 DATA CONTRACT (confirmed 2026-07-23 via Supabase audit, 264 analyzed articles)
─────────────────────────────────────────────────────────────────────────────────────
All 10 Sprint 3 fields present in 100% of analyzed records:
  signal_type, importance, sentiment, entities, one_line_summary,
  why_it_matters, business_implication, suggested_action, target_persona, urgency

Field notes:
  - entities   : list[str] of company/entity names — already tagged, zero extra cost
  - source     : single string value (e.g. "BBC News"), NOT a count
  - published_at: use for "new today" KPI (analyzed_at lags by batch run time)

LOCKED DECISIONS (M4.0)
─────────────────────────────────────────────────────────────────────────────────────
DEFAULT FILTER  : urgency >= 7 OR importance >= 7  (119/264 = 45% of corpus)
                  NOTE: filter and sort use separate logic — do NOT conflate them.
DEFAULT SORT    : priority_score = urgency + importance, descending (equal weight;
                  urgency captures time-sensitivity, importance captures strategic weight)
TAXONOMY        : signal_type is the ONE taxonomy used everywhere (pills + lens chart).
                  Meta-views ("Top Signals", "BD Opportunities") are cross-cutting
                  overlays, NOT separate categories.
RANKING VOCAB   : urgency = time-sensitive immediacy (1–10)
                  importance = strategic weight (1–10)
                  priority_score = urgency + importance (sort key only, max 20)
                  "High Priority" KPI count = articles where urgency>=7 OR importance>=7
KPI CARD SWAP   : "Avg Importance" → "High Priority" (count of signals ≥ threshold)
                  "Positive Tone" KPI is scoped to current view, not global corpus.
SIGNAL CARD FOOTER: source · published_date · Read full article link (no source count)
SENTIMENT       : shown once (in KPI + per-card); global sentiment chart removed.
"""

import os
from datetime import datetime, timezone, timedelta

import requests
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE = os.getenv("API_BASE", "http://localhost:8000")

# Default view filter (locked M4.0): OR logic — either dimension alone qualifies
# urgency >= 7 OR importance >= 7  →  119/264 articles (45%)
MIN_URGENCY = 7
MIN_IMPORTANCE = 7

# High Priority KPI count uses same OR threshold
def is_high_priority(article: dict) -> bool:
    return (article.get("urgency") or 0) >= MIN_URGENCY or \
           (article.get("importance") or 0) >= MIN_IMPORTANCE

# Signal taxonomy — single source of truth for pills, lens chart, and filters
SIGNAL_TAXONOMY = [
    "product_launch",
    "competitor_move",
    "funding_event",
    "regulatory_risk",
    "enterprise_adoption",
    "partnership",
    "market_expansion",
    "research_breakthrough",
    "leadership_change",
    "pricing_change",
    "hiring_growth",
    "security_incident",
    "other",
]

# Meta-views (cross-cutting lenses, NOT taxonomy categories)
META_VIEWS = {
    "top_signals":    {"label": "Top Signals",       "high_priority_or": True},
    "bd_opps":        {"label": "BD Opportunities",  "signal_types": ["partnership", "enterprise_adoption", "market_expansion", "funding_event"]},
    "regulatory":     {"label": "Regulatory Risk",   "signal_types": ["regulatory_risk"]},
    "competitor":     {"label": "Competitor Moves",  "signal_types": ["competitor_move"]},
    "funding":        {"label": "Funding & Startups","signal_types": ["funding_event", "market_expansion"]},
}

# ── Central state (single source of truth for all filters) ───────────────────

def init_state():
    if "view" not in st.session_state:
        st.session_state.view = {
            "meta_view":     "top_signals",   # active meta-view key from META_VIEWS
            "signal_types":  [],              # [] = all types; list[str] = filter to these
            "sentiment":     None,            # None = all; "positive"/"negative"/"neutral"
            "search_query":  "",              # free-text search against title + entities
            # filter thresholds — OR logic: either condition alone qualifies
            "min_urgency":   MIN_URGENCY,     # default 7
            "min_importance": MIN_IMPORTANCE, # default 7
        }

init_state()

# ── Data fetching ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def fetch_stats() -> dict:
    return requests.get(f"{API_BASE}/events/stats", timeout=10).json()


@st.cache_data(ttl=60)
def fetch_all_events(limit: int = 264) -> list[dict]:
    """Fetch all analyzed events; client-side filtering applied after."""
    result = requests.get(f"{API_BASE}/events", params={"limit": limit}, timeout=10).json()
    if not isinstance(result, list):
        return []
    return result


def apply_filters(articles: list[dict]) -> list[dict]:
    """Apply current view_state filters and return sorted articles."""
    view = st.session_state.view
    out = articles

    # Meta-view filter
    meta = META_VIEWS.get(view["meta_view"], {})
    if meta.get("high_priority_or"):
        # Default view: urgency >= min_urgency OR importance >= min_importance
        mu = view.get("min_urgency", MIN_URGENCY)
        mi = view.get("min_importance", MIN_IMPORTANCE)
        out = [a for a in out if (a.get("urgency") or 0) >= mu or (a.get("importance") or 0) >= mi]
    if "signal_types" in meta:
        out = [a for a in out if a.get("signal_type") in meta["signal_types"]]

    # Manual signal_type override (from pill clicks, future M4.1)
    if view["signal_types"]:
        out = [a for a in out if a.get("signal_type") in view["signal_types"]]

    # Sentiment filter
    if view["sentiment"]:
        out = [a for a in out if a.get("sentiment") == view["sentiment"]]

    # Search filter (title + entities)
    q = view["search_query"].strip().lower()
    if q:
        def matches(a):
            if q in (a.get("title") or "").lower():
                return True
            entities = a.get("entities") or []
            return any(q in e.lower() for e in entities)
        out = [a for a in out if matches(a)]

    # Sort by priority_score descending
    out = sorted(out, key=lambda a: (a.get("urgency") or 0) + (a.get("importance") or 0), reverse=True)
    return out


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Nestor Insight", page_icon="🔍", layout="wide")
st.title("🔍 AI Industry Signal Monitoring")
st.caption("Find targeted AI market signals for BD and market analysis")

# ── Load data ─────────────────────────────────────────────────────────────────

try:
    stats = fetch_stats()
except Exception as e:
    st.error(f"Cannot connect to API ({API_BASE}): {e}\n\nPlease start: `uvicorn app.api.main:app --port 8000`")
    st.stop()

try:
    all_articles = fetch_all_events()
    if not all_articles:
        st.warning("No analyzed articles returned from API.")
except Exception as e:
    st.error(f"Failed to load articles: {e}")
    all_articles = []

filtered_articles = apply_filters(all_articles)

# ── KPI cards ─────────────────────────────────────────────────────────────────

total = stats.get("total", 0)
by_sentiment = stats.get("by_sentiment", {})
high_priority_count = sum(1 for a in all_articles if is_high_priority(a))

# "New today" = published_at within last 24h
cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
new_today = sum(
    1 for a in all_articles
    if a.get("published_at") and datetime.fromisoformat(a["published_at"]) >= cutoff
)

# Positive tone scoped to current filtered view
view_total = len(filtered_articles)
view_positive = sum(1 for a in filtered_articles if a.get("sentiment") == "positive")
view_positive_pct = round(view_positive / view_total * 100, 1) if view_total else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Signals", total)
c2.metric("High Priority", high_priority_count)
c3.metric("New (24h)", new_today)
c4.metric("Positive Tone", f"{view_positive_pct}%", help="Scoped to current view")

st.divider()

# ── Charts ────────────────────────────────────────────────────────────────────

col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Signal Type Distribution")
    by_signal = stats.get("by_signal_type") or stats.get("by_event_type", {})
    if by_signal:
        st.bar_chart(by_signal)
    else:
        st.info("No data available")

with col_right:
    st.subheader("Sentiment Distribution")
    if by_sentiment:
        import pandas as pd
        labels = list(by_sentiment.keys())
        values = list(by_sentiment.values())
        df_sentiment = pd.DataFrame({"Sentiment": labels, "Count": values})
        st.dataframe(df_sentiment.set_index("Sentiment"), use_container_width=True)
        for label, val in zip(labels, values):
            pct = val / total if total else 0
            st.progress(pct, text=f"{label}: {val} articles ({pct*100:.1f}%)")
    else:
        st.info("No data available")

st.divider()

# ── Signal list (placeholder — M4.1 will replace with full UI) ───────────────

st.subheader("High Relevance Signals")
st.caption(f"{view_total} signals in current view")

for article in filtered_articles:
    importance = article.get("importance") or 0
    urgency = article.get("urgency") or 0
    priority_score = urgency + importance
    sentiment = article.get("sentiment") or "unknown"
    sentiment_emoji = {"positive": "🟢", "negative": "🔴", "neutral": "🟡"}.get(sentiment, "⚪")
    published = (article.get("published_at") or "")[:10]

    with st.container(border=True):
        col_main, col_meta = st.columns([4, 1])
        with col_main:
            st.markdown(f"**{article.get('title', '(no title)')}**")
            summary = article.get("one_line_summary")
            if summary:
                st.caption(summary)
            why = article.get("why_it_matters")
            if why:
                st.markdown(f"**Why it matters:** {why}")
            implication = article.get("business_implication")
            if implication:
                st.markdown(f"**Business implication:** {implication}")
            action = article.get("suggested_action")
            if action:
                st.markdown(f"**Suggested action:** {action}")
            url = article.get("url", "")
            source = article.get("source") or ""
            st.caption(f"{source} · {published}" + (f" · [Read full article →]({url})" if url else ""))
        with col_meta:
            st.markdown(f"{sentiment_emoji} {sentiment}")
            st.markdown(f"Score **{priority_score}**/20")
            st.caption(f"U:{urgency} I:{importance}")
            persona = article.get("target_persona")
            if persona:
                st.caption(persona)
            signal_type = article.get("signal_type")
            if signal_type:
                st.caption(f"`{signal_type}`")
