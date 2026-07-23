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
DEFAULT SORT    : priority_score = urgency + importance, descending (equal weight)
TAXONOMY        : signal_type is the ONE taxonomy (pills + lens chart).
                  Meta-views (top_signals, bd_opps) are cross-cutting overlays only.
RANKING VOCAB   : urgency = immediacy · importance = strategic weight
                  priority_score = urgency + importance (sort key only, max 20)
KPI CARD SWAP   : "Avg Importance" → "High Priority" (count via is_high_priority())
                  "Positive Tone" scoped to current view.
SIGNAL CARD FOOTER: source · published_date · Read full article (no source count)
SENTIMENT       : shown once (KPI + per-card); global sentiment chart removed M4.2.
"""

import os
from datetime import datetime, timezone, timedelta

import requests
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE = os.getenv("API_BASE", "http://localhost:8000")

# Default view filter (OR logic — either dimension alone qualifies)
# urgency >= 7 OR importance >= 7  →  119/264 articles (45%)
MIN_URGENCY = 7
MIN_IMPORTANCE = 7


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

# True meta-views: cross-cutting overlays (not signal-type categories)
META_VIEWS = {
    "top_signals": {"label": "Top Signals",      "high_priority_or": True},
    "bd_opps":     {"label": "BD Opportunities", "signal_types": ["partnership", "enterprise_adoption", "market_expansion", "funding_event"]},
}

# Ordered pill options — meta-views first, then signal-type filters
# Meta pills set meta_view; signal pills set signal_types (via PILL_OPTIONS config)
PILL_OPTIONS = {
    "Top Signals":        {"type": "meta",   "meta_key": "top_signals"},
    "BD Opportunities":   {"type": "meta",   "meta_key": "bd_opps"},
    "Regulatory Risk":    {"type": "signal", "signal_type": "regulatory_risk"},
    "Competitor Moves":   {"type": "signal", "signal_type": "competitor_move"},
    "Funding & Startups": {"type": "signal", "signal_type": "funding_event"},
    "Product Launches":   {"type": "signal", "signal_type": "product_launch"},
}

# Human-readable labels for signal_type values
SIGNAL_TYPE_LABELS = {
    "product_launch":        "Product Launch",
    "competitor_move":       "Competitor Move",
    "funding_event":         "Funding",
    "regulatory_risk":       "Regulatory Risk",
    "enterprise_adoption":   "Enterprise Adoption",
    "partnership":           "Partnership",
    "market_expansion":      "Market Expansion",
    "research_breakthrough": "Research Breakthrough",
    "leadership_change":     "Leadership Change",
    "pricing_change":        "Pricing Change",
    "hiring_growth":         "Hiring Growth",
    "security_incident":     "Security Incident",
    "other":                 "Other",
}


def _urgency_color(u: int) -> str:
    """Accent scale: amber = act now, blue = act soon, slate = monitor. Never red."""
    if u >= 9: return "#F59E0B"   # amber
    if u >= 7: return "#3B82F6"   # blue
    if u >= 5: return "#64748B"   # slate
    return "#94A3B8"              # muted


def _chip(text: str, accent: bool = False) -> str:
    border = "#F59E0B" if accent else "#64748B"
    color  = "#F59E0B" if accent else "#64748B"
    weight = "600" if accent else "400"
    return (
        f'<span style="border:1px solid {border};color:{color};padding:2px 9px;'
        f'border-radius:999px;font-size:0.75rem;font-weight:{weight};white-space:nowrap">'
        f'{text}</span>'
    )


def render_card(article: dict) -> None:
    urgency     = article.get("urgency") or 0
    signal_type = article.get("signal_type") or ""
    persona     = (article.get("target_persona") or "").replace("_", " ")
    high_prio   = is_high_priority(article)
    sentiment   = article.get("sentiment") or "unknown"
    sentiment_emoji = {"positive": "🟢", "negative": "🔴", "neutral": "🟡"}.get(sentiment, "⚪")
    published   = (article.get("published_at") or "")[:10]
    url         = article.get("url") or ""
    source      = article.get("source") or ""

    with st.container(border=True):
        col_main, col_right = st.columns([5, 1])

        with col_right:
            color = _urgency_color(urgency)
            st.markdown(
                f'<div style="text-align:center;padding-top:8px">'
                f'<span style="font-size:2rem;font-weight:700;color:{color}">{urgency}</span>'
                f'<span style="font-size:0.85rem;color:#94A3B8">/10</span><br>'
                f'<span style="font-size:0.65rem;color:#94A3B8;letter-spacing:0.08em;'
                f'text-transform:uppercase">urgency</span><br><br>'
                f'<span style="font-size:1.1rem">{sentiment_emoji}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with col_main:
            # Tags row
            chips = []
            if signal_type:
                label = SIGNAL_TYPE_LABELS.get(signal_type, signal_type.replace("_", " ").title())
                chips.append(_chip(label))
            if persona:
                chips.append(_chip(persona))
            if high_prio:
                chips.append(_chip("⚑ High Priority", accent=True))
            if chips:
                st.markdown(
                    '<div style="margin-bottom:4px">' + "&nbsp;&nbsp;".join(chips) + "</div>",
                    unsafe_allow_html=True,
                )

            # Title + one-line summary
            st.markdown(f"**{article.get('title') or '(no title)'}**")
            summary = article.get("one_line_summary")
            if summary:
                st.caption(summary)

            # Why it matters + Suggested action — always visible (key decisions)
            why = article.get("why_it_matters")
            if why:
                st.markdown(f"⚡ **Why it matters** — {why}")
            action = article.get("suggested_action")
            if action:
                st.markdown(f"→ **Suggested action** — {action}")

            # Business implication — secondary, in expander to keep card scannable
            implication = article.get("business_implication")
            if implication:
                with st.expander("Business implication"):
                    st.write(implication)

            # Footer: source · date · Read full article (no source count)
            footer_parts = [p for p in [source, published] if p]
            footer = " · ".join(footer_parts)
            if url:
                footer += f" · [Read full article →]({url})"
            if footer:
                st.caption(footer)


# ── Central state (single source of truth for all filters) ───────────────────

def init_state():
    if "view" not in st.session_state:
        st.session_state.view = {
            "meta_view":      "top_signals",  # overlay key from META_VIEWS
            "signal_types":   [],             # [] = all; list[str] = filter to these
            "sentiment":      None,           # None = all
            "search_query":   "",             # free-text over title + summary + entities
            "min_urgency":    MIN_URGENCY,    # OR logic — either alone qualifies
            "min_importance": MIN_IMPORTANCE,
            "show_all":       False,          # True = bypass priority filter
        }


def _active_pill() -> str:
    """Return the pill label that reflects current session_state.view."""
    view = st.session_state.view
    if view["signal_types"]:
        stype = view["signal_types"][0]
        for label, cfg in PILL_OPTIONS.items():
            if cfg.get("signal_type") == stype:
                return label
    for label, cfg in PILL_OPTIONS.items():
        if cfg.get("type") == "meta" and cfg.get("meta_key") == view["meta_view"]:
            return label
    return "Top Signals"


def _on_search_change():
    q = st.session_state["_search_input"]
    st.session_state.view["search_query"] = q
    if q.strip():
        # Search is now the active narrowing — reset pill to default overlay
        st.session_state.view["meta_view"] = "top_signals"
        st.session_state.view["signal_types"] = []
        st.session_state["_pill_selector"] = "Top Signals"


def _on_pill_change():
    label = st.session_state["_pill_selector"]
    if label is None:
        return
    # Pill clears search (only one narrowing active at a time)
    st.session_state.view["search_query"] = ""
    st.session_state["_search_input"] = ""
    cfg = PILL_OPTIONS.get(label, {})
    if cfg.get("type") == "meta":
        st.session_state.view["meta_view"] = cfg["meta_key"]
        st.session_state.view["signal_types"] = []
    elif cfg.get("type") == "signal":
        st.session_state.view["signal_types"] = [cfg["signal_type"]]
        st.session_state.view["meta_view"] = "top_signals"


def _toggle_show_all():
    st.session_state.view["show_all"] = st.session_state._show_all_toggle


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
    """Single filtering entry point — every feature reads session_state.view."""
    view = st.session_state.view
    out = articles

    # Meta-view filter (skipped when show_all is active)
    if not view.get("show_all"):
        meta = META_VIEWS.get(view["meta_view"], {})
        if meta.get("high_priority_or"):
            mu = view.get("min_urgency", MIN_URGENCY)
            mi = view.get("min_importance", MIN_IMPORTANCE)
            out = [a for a in out if (a.get("urgency") or 0) >= mu or (a.get("importance") or 0) >= mi]
        if "signal_types" in meta:
            out = [a for a in out if a.get("signal_type") in meta["signal_types"]]

    # Signal-type pill override (always applied, independent of show_all)
    if view["signal_types"]:
        out = [a for a in out if a.get("signal_type") in view["signal_types"]]

    # Sentiment filter
    if view["sentiment"]:
        out = [a for a in out if a.get("sentiment") == view["sentiment"]]

    # Search filter: title + one_line_summary + entities (substring, case-insensitive)
    q = view["search_query"].strip().lower()
    if q:
        def matches(a):
            if q in (a.get("title") or "").lower():
                return True
            if q in (a.get("one_line_summary") or "").lower():
                return True
            return any(q in e.lower() for e in (a.get("entities") or []))
        out = [a for a in out if matches(a)]

    # Sort by priority_score descending (sort key only, never an inclusion gate)
    out = sorted(out, key=lambda a: (a.get("urgency") or 0) + (a.get("importance") or 0), reverse=True)
    return out


# ── Page setup ────────────────────────────────────────────────────────────────

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

# ── Search + pills (rendered before apply_filters so callbacks fire first) ────

st.text_input(
    "What are you tracking?",
    placeholder="AI agents, OpenAI, regulation, funding, enterprise adoption...",
    key="_search_input",
    on_change=_on_search_change,
)

st.pills(
    "Quick View",
    options=list(PILL_OPTIONS.keys()),
    default=_active_pill(),
    key="_pill_selector",
    on_change=_on_pill_change,
    label_visibility="collapsed",
)

# Apply filters after search/pill callbacks have updated session_state.view
filtered_articles = apply_filters(all_articles)

# ── KPI cards ─────────────────────────────────────────────────────────────────

total = stats.get("total", 0)
high_priority_count = sum(1 for a in all_articles if is_high_priority(a))

cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
new_today = sum(
    1 for a in all_articles
    if a.get("published_at") and datetime.fromisoformat(a["published_at"]) >= cutoff
)

view_total = len(filtered_articles)
view_positive = sum(1 for a in filtered_articles if a.get("sentiment") == "positive")
view_positive_pct = round(view_positive / view_total * 100, 1) if view_total else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Signals", total)
c2.metric("High Priority", high_priority_count)
c3.metric("New (24h)", new_today)
c4.metric("Positive Tone", f"{view_positive_pct}%", help="Scoped to current view")

st.divider()

# ── Signal Type Distribution (sentiment chart removed M4.2) ───────────────────

st.subheader("Signal Type Distribution")
by_signal = stats.get("by_signal_type") or stats.get("by_event_type", {})
if by_signal:
    st.bar_chart(by_signal)
else:
    st.info("No data available")

st.divider()

# ── Signal list ──────────────────────────────────────────────────────────────

st.subheader("High Relevance Signals")

col_hdr, col_toggle = st.columns([3, 1])
with col_hdr:
    view = st.session_state.view
    q = view.get("search_query", "")
    if view.get("show_all"):
        st.caption(f"All {view_total} signals · sorted by priority score")
    elif q:
        st.caption(f"{view_total} results for \"{q}\" · sorted by priority score")
    else:
        st.caption(f"{view_total} signals · sorted by priority score")
with col_toggle:
    st.toggle(
        "View all signals",
        value=st.session_state.view.get("show_all", False),
        key="_show_all_toggle",
        on_change=_toggle_show_all,
    )

if not filtered_articles:
    st.info("No signals match your current filters.")
else:
    for article in filtered_articles:
        render_card(article)
