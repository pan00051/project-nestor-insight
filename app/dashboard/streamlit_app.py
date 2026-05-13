import requests
import streamlit as st

API_BASE = "http://localhost:8000"


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
st.set_page_config(page_title="Nestor Insight", page_icon="📰", layout="wide")
st.title("📰 Nestor Insight Dashboard")

try:
    stats = fetch_stats()
except Exception as e:
    st.error(f"无法连接 API（{API_BASE}）：{e}\n\n请先运行：`uvicorn app.api.main:app --port 8000`")
    st.stop()

# ── 统计卡片 ──────────────────────────────────────────────────────────────────
total = stats.get("total", 0)
avg_imp = stats.get("avg_importance") or 0
by_sentiment = stats.get("by_sentiment", {})
positive_pct = round(by_sentiment.get("positive", 0) / total * 100, 1) if total else 0
negative_pct = round(by_sentiment.get("negative", 0) / total * 100, 1) if total else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("总文章数", total)
c2.metric("平均重要性", f"{avg_imp:.1f} / 10")
c3.metric("正面情绪", f"{positive_pct}%")
c4.metric("负面情绪", f"{negative_pct}%")

st.divider()

# ── 图表 ──────────────────────────────────────────────────────────────────────
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("文章分类分布")
    by_type = stats.get("by_event_type", {})
    if by_type:
        st.bar_chart(by_type)
    else:
        st.info("暂无数据")

with col_right:
    st.subheader("情绪分布")
    if by_sentiment:
        # st.plotly_chart not available without plotly; use native pie via altair-free workaround
        labels = list(by_sentiment.keys())
        values = list(by_sentiment.values())
        pie_data = {"情绪": labels, "数量": values}
        import pandas as pd
        df_pie = pd.DataFrame(pie_data)
        st.dataframe(
            df_pie.set_index("情绪"),
            use_container_width=True,
        )
        # simple visual fallback: progress bars
        for label, val in zip(labels, values):
            pct = val / total if total else 0
            st.progress(pct, text=f"{label}：{val} 篇（{pct*100:.1f}%）")
    else:
        st.info("暂无数据")

st.divider()

# ── 筛选区 ────────────────────────────────────────────────────────────────────
st.subheader("文章列表")

f1, f2 = st.columns(2)
with f1:
    category_options = ["All", "technology", "politics", "business", "science", "other"]
    selected_category = st.selectbox("分类", category_options)

with f2:
    sentiment_options = ["All", "positive", "negative", "neutral"]
    selected_sentiment = st.selectbox("情绪", sentiment_options)

category_param = None if selected_category == "All" else selected_category
sentiment_param = None if selected_sentiment == "All" else selected_sentiment

# ── 文章列表 ──────────────────────────────────────────────────────────────────
try:
    articles = fetch_events(category_param, sentiment_param)
except Exception as e:
    st.error(f"加载文章失败：{e}")
    articles = []

articles_sorted = sorted(articles, key=lambda x: x.get("importance") or 0, reverse=True)

if not articles_sorted:
    st.info("没有符合条件的文章。")
else:
    st.caption(f"共 {len(articles_sorted)} 篇")
    for article in articles_sorted:
        importance = article.get("importance") or 0
        sentiment = article.get("sentiment") or "unknown"
        sentiment_emoji = {"positive": "🟢", "negative": "🔴", "neutral": "🟡"}.get(sentiment, "⚪")

        with st.container(border=True):
            col_main, col_meta = st.columns([4, 1])
            with col_main:
                st.markdown(f"**{article.get('title', '(无标题)')}**")
                summary = article.get("one_line_summary")
                if summary:
                    st.caption(summary)
            with col_meta:
                st.markdown(f"{sentiment_emoji} {sentiment}")
                st.markdown(f"重要性 **{importance}**/10")
                st.caption(article.get("source") or "")
