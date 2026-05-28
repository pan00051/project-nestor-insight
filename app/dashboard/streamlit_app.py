import os

import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://localhost:8000")


@st.cache_data(ttl=60)
def fetch_stats() -> dict:
    return requests.get(f"{API_BASE}/events/stats", timeout=10).json()


@st.cache_data(ttl=60)
def fetch_events(category: str | None, sentiment: str | None, limit: int = 100) -> list[dict]:
    params = {"limit": limit}
    if category:
        params["category"] = category
    if sentiment:
        params["sentiment"] = sentiment
    return requests.get(f"{API_BASE}/events", params=params, timeout=10).json()


# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Nestor Insight", page_icon="🔍", layout="wide")
st.title("🔍 AI Industry Signal Monitoring")
st.caption("Structured AI industry monitoring and explainable signal analysis")

try:
    stats = fetch_stats()
except Exception as e:
    st.error(f"Cannot connect to API ({API_BASE}): {e}\n\nPlease start the API first: `uvicorn app.api.main:app --port 8000`")
    st.stop()

# ── Metrics ───────────────────────────────────────────────────────────────────
total = stats.get("total", 0)
avg_imp = stats.get("avg_importance") or 0
by_sentiment = stats.get("by_sentiment", {})
positive_pct = round(by_sentiment.get("positive", 0) / total * 100, 1) if total else 0
negative_pct = round(by_sentiment.get("negative", 0) / total * 100, 1) if total else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Articles", total)
c2.metric("Avg Importance", f"{avg_imp:.1f} / 10")
c3.metric("Positive", f"{positive_pct}%")
c4.metric("Negative", f"{negative_pct}%")

st.divider()

# ── Charts ────────────────────────────────────────────────────────────────────
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Signal Type Distribution")
    by_type = stats.get("by_signal_type") or stats.get("by_event_type", {})
    if by_type:
        st.bar_chart(by_type)
    else:
        st.info("No data available")

with col_right:
    st.subheader("Sentiment Distribution")
    if by_sentiment:
        labels = list(by_sentiment.keys())
        values = list(by_sentiment.values())
        import pandas as pd
        df_pie = pd.DataFrame({"Sentiment": labels, "Count": values})
        st.dataframe(
            df_pie.set_index("Sentiment"),
            use_container_width=True,
        )
        for label, val in zip(labels, values):
            pct = val / total if total else 0
            st.progress(pct, text=f"{label}: {val} articles ({pct*100:.1f}%)")
    else:
        st.info("No data available")

st.divider()

# ── Filters & article list ────────────────────────────────────────────────────
st.subheader("High Relevance Signals")

f1, f2 = st.columns(2)
with f1:
    category_options = ["technology", "All", "politics", "business", "science", "other"]
    selected_category = st.selectbox("Category", category_options)

with f2:
    sentiment_options = ["All", "positive", "negative", "neutral"]
    selected_sentiment = st.selectbox("Sentiment", sentiment_options)

category_param = None if selected_category == "All" else selected_category
sentiment_param = None if selected_sentiment == "All" else selected_sentiment

# ── Article list ──────────────────────────────────────────────────────────────
try:
    articles = fetch_events(category_param, sentiment_param)
except Exception as e:
    st.error(f"Failed to load articles: {e}")
    articles = []

articles_sorted = sorted(
    articles,
    key=lambda x: (x.get("urgency") or 0, x.get("importance") or 0),
    reverse=True,
)

if not articles_sorted:
    st.info("No articles found.")
else:
    st.caption(f"{len(articles_sorted)} articles found")
    for article in articles_sorted:
        importance = article.get("importance") or 0
        urgency = article.get("urgency") or 0
        sentiment = article.get("sentiment") or "unknown"
        sentiment_emoji = {"positive": "🟢", "negative": "🔴", "neutral": "🟡"}.get(sentiment, "⚪")

        with st.container(border=True):
            col_main, col_meta = st.columns([4, 1])
            with col_main:
                st.markdown(f"**{article.get('title', '(no title)')}**")
                summary = article.get("one_line_summary")
                if summary:
                    st.caption(summary)
                signal_type = article.get("signal_type")
                if signal_type:
                    st.markdown(f"**Signal:** `{signal_type}`")
                why_it_matters = article.get("why_it_matters")
                if why_it_matters:
                    st.markdown(f"**Why it matters:** {why_it_matters}")
                business_implication = article.get("business_implication")
                if business_implication:
                    st.markdown(f"**Business implication:** {business_implication}")
                suggested_action = article.get("suggested_action")
                if suggested_action:
                    st.markdown(f"**Suggested action:** {suggested_action}")
                url = article.get("url", "")
                if url:
                    st.markdown(f"[Read full article →]({url})")
            with col_meta:
                st.markdown(f"{sentiment_emoji} {sentiment}")
                st.markdown(f"Urgency **{urgency}**/10")
                st.markdown(f"Importance **{importance}**/10")
                target_persona = article.get("target_persona")
                if target_persona:
                    st.caption(f"Target: {target_persona}")
                st.caption(article.get("source") or "")
