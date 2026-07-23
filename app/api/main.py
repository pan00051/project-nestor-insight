import os
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from supabase import create_client

load_dotenv()

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

app = FastAPI(title="Nestor Insight API", version="0.2.0")

ARTICLE_FIELDS = (
    "id,title,url,source,published_at,event_type,sentiment,importance,entities,"
    "one_line_summary,signal_type,why_it_matters,business_implication,"
    "suggested_action,target_persona,urgency"
)
STATS_PAGE_SIZE = 500


# ---------- Models ----------

class ArticleSummary(BaseModel):
    id: int
    title: Optional[str]
    source: Optional[str]
    published_at: Optional[datetime]
    event_type: Optional[str]
    sentiment: Optional[str]
    importance: Optional[int]
    entities: Optional[list[str]]
    url: Optional[str]
    one_line_summary: Optional[str]
    signal_type: Optional[str]
    why_it_matters: Optional[str]
    business_implication: Optional[str]
    suggested_action: Optional[str]
    target_persona: Optional[str]
    urgency: Optional[int]


class ArticleFull(ArticleSummary):
    url: Optional[str]
    summary: Optional[str]
    analyzed_at: Optional[datetime]
    feed_url: Optional[str]


class StatsResponse(BaseModel):
    total: int
    avg_importance: Optional[float]
    by_event_type: dict[str, int]
    by_signal_type: dict[str, int]
    by_sentiment: dict[str, int]


# ---------- Routes ----------

@app.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0"}


@app.get("/events", response_model=list[ArticleSummary])
def list_events(
    category: Optional[str] = Query(None, description="Filter by event_type"),
    sentiment: Optional[str] = Query(None, description="positive / negative / neutral"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    query = (
        supabase.table("articles")
        .select(ARTICLE_FIELDS)
        .not_.is_("analyzed_at", "null")
        .order("published_at", desc=True)
        .range(offset, offset + limit - 1)
    )
    if category:
        query = query.eq("event_type", category)
    if sentiment:
        query = query.eq("sentiment", sentiment)

    result = query.execute()
    return result.data


@app.get("/events/stats", response_model=StatsResponse)
def get_stats():
    rows = fetch_all_stats_rows()

    by_event_type: dict[str, int] = {}
    by_signal_type: dict[str, int] = {}
    by_sentiment: dict[str, int] = {}
    importance_sum = 0
    importance_count = 0

    for row in rows:
        et = row.get("event_type") or "unknown"
        by_event_type[et] = by_event_type.get(et, 0) + 1

        signal_type = row.get("signal_type") or "unknown"
        by_signal_type[signal_type] = by_signal_type.get(signal_type, 0) + 1

        s = row.get("sentiment") or "unknown"
        by_sentiment[s] = by_sentiment.get(s, 0) + 1

        imp = row.get("importance")
        if imp is not None:
            importance_sum += imp
            importance_count += 1

    return StatsResponse(
        total=len(rows),
        avg_importance=round(importance_sum / importance_count, 2) if importance_count else None,
        by_event_type=by_event_type,
        by_signal_type=by_signal_type,
        by_sentiment=by_sentiment,
    )


def fetch_all_stats_rows() -> list[dict]:
    rows: list[dict] = []
    offset = 0

    while True:
        result = (
            supabase.table("articles")
            .select("id,event_type,signal_type,sentiment,importance")
            .not_.is_("analyzed_at", "null")
            .order("id")
            .range(offset, offset + STATS_PAGE_SIZE - 1)
            .execute()
        )
        page = result.data if isinstance(result.data, list) else []
        rows.extend(page)
        if len(page) < STATS_PAGE_SIZE:
            break
        offset += STATS_PAGE_SIZE

    return rows


@app.get("/events/{article_id}", response_model=ArticleFull)
def get_event(article_id: int):
    result = (
        supabase.table("articles")
        .select("*")
        .eq("id", article_id)
        .not_.is_("analyzed_at", "null")
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Article not found")
    return result.data[0]
