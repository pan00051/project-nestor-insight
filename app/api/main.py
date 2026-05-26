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

ARTICLE_FIELDS = "id,title,url,source,published_at,event_type,sentiment,importance,entities,one_line_summary"


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
    one_line_summary: Optional[str]


class ArticleFull(ArticleSummary):
    url: Optional[str]
    summary: Optional[str]
    analyzed_at: Optional[datetime]
    feed_url: Optional[str]


class StatsResponse(BaseModel):
    total: int
    avg_importance: Optional[float]
    by_event_type: dict[str, int]
    by_sentiment: dict[str, int]


# ---------- Routes ----------

@app.get("/health")
def health():
    return {"status": "ok"}


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
    result = (
        supabase.table("articles")
        .select("event_type,sentiment,importance")
        .not_.is_("analyzed_at", "null")
        .execute()
    )
    rows = result.data

    by_event_type: dict[str, int] = {}
    by_sentiment: dict[str, int] = {}
    importance_sum = 0
    importance_count = 0

    for row in rows:
        et = row.get("event_type") or "unknown"
        by_event_type[et] = by_event_type.get(et, 0) + 1

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
        by_sentiment=by_sentiment,
    )


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
