# Nestor Insight
> AI-powered news intelligence platform — automatically collects, analyzes, and visualizes public news events.

## What it does

Nestor Insight is an MVP that turns raw news into structured intelligence:

1. **Collect** — Fetches articles from RSS feeds (BBC, TechCrunch, Hacker News) every run
2. **Analyze** — Uses Claude AI to classify each article: event type, sentiment, importance score (1–10), key entities, one-line summary
3. **Store** — Persists structured results in PostgreSQL (Supabase)
4. **Visualize** — Streamlit dashboard with filters, charts, and ranked article list

## Architecture

```
RSS Feeds → rss_collector.py → Supabase (PostgreSQL)
                                      ↓
                            ai_analyzer.py (Claude API)
                                      ↓
                            FastAPI (/events, /stats)
                                      ↓
                          Streamlit Dashboard
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Data collection | feedparser, httpx |
| AI analysis | Anthropic Claude (Haiku) |
| Database | Supabase (PostgreSQL) |
| API | FastAPI + Pydantic |
| Dashboard | Streamlit |
| Package management | uv |

## Project Structure

```
nestor-insight/
├── app/
│   ├── collector/        # RSS fetching
│   │   └── rss_collector.py
│   ├── analyzer/         # AI analysis pipeline
│   │   └── ai_analyzer.py
│   ├── api/              # FastAPI endpoints
│   │   └── main.py
│   └── dashboard/        # Streamlit UI
│       └── streamlit_app.py
├── .env                  # API keys (not committed)
└── pyproject.toml
```

## Setup

**Requirements:** Python 3.11+, uv

```bash
git clone https://github.com/pan00051/project-nestor-insight
cd project-nestor-insight
uv venv --python python3.11
source .venv/bin/activate
uv sync
```

Create `.env`:

```
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
ANTHROPIC_API_KEY=your_anthropic_key
```

## Usage

```bash
# 1. Collect news
python -m app.collector.rss_collector

# 2. Run AI analysis
python -m app.analyzer.ai_analyzer

# 3. Start API (Terminal 1)
uvicorn app.api.main:app --port 8000

# 4. Start Dashboard (Terminal 2)
streamlit run app/dashboard/streamlit_app.py
```

Dashboard: http://localhost:8501  
API docs: http://localhost:8000/docs

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /events` | List articles (filter by category, sentiment) |
| `GET /events/stats` | Aggregated statistics |
| `GET /events/{id}` | Single article detail |
| `GET /health` | Health check |

## Roadmap

- [ ] Scheduled auto-collection (APScheduler)
- [ ] Semantic search (pgvector + RAG)
- [ ] Multi-source support (Twitter, Reddit)
- [ ] Email/notification alerts
- [ ] Deployment (Railway)

## About

Built as an AI PM portfolio project. Demonstrates end-to-end AI product development: data pipeline design, LLM integration, API architecture, and data visualization.
