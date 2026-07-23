import argparse
import html
import json
import os
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Literal

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from supabase import create_client

load_dotenv()

MODEL_NAME = "claude-haiku-4-5-20251001"
DEFAULT_BATCH_LIMIT = 100
DEFAULT_MIN_RELEVANCE = 2
FETCH_PAGE_SIZE = 100
MAX_ANALYSIS_ATTEMPTS = 2

EVENT_TYPES = ("technology", "politics", "business", "science", "other")
SENTIMENTS = ("positive", "negative", "neutral")
SIGNAL_TYPES = (
    "funding_event",
    "product_launch",
    "leadership_change",
    "market_expansion",
    "partnership",
    "hiring_growth",
    "regulatory_risk",
    "competitor_move",
    "enterprise_adoption",
    "security_incident",
    "pricing_change",
    "research_breakthrough",
    "other",
)
TARGET_PERSONAS = (
    "founder",
    "product_leader",
    "sales_leader",
    "marketing_leader",
    "investor",
    "analyst",
    "engineer",
    "policy_leader",
    "other",
)

# Strong terms can qualify an article alone. Supporting terms need a combination.
STRONG_AI_TERMS = (
    "artificial intelligence",
    "generative ai",
    "machine learning",
    "deep learning",
    "large language model",
    "foundation model",
    "multimodal model",
    "neural network",
    "computer vision",
    "openai",
    "anthropic",
    "chatgpt",
    "claude",
    "gemini",
    "deepmind",
    "mistral",
    "perplexity",
    "hugging face",
    "stability ai",
    "midjourney",
    "copilot",
)
SUPPORTING_AI_TERMS = (
    "llm",
    "ai agent",
    "agentic",
    "inference",
    "training",
    "model",
    "automation",
    "algorithm",
    "robotics",
    "gpu",
    "ai chip",
    "data center",
    "nvidia",
    "xai",
    "cohere",
    "grok",
    "cursor",
)

PROMPT_TEMPLATE = """\
Analyze the following AI industry news article as a business development and market intelligence signal.
Return ONLY a JSON object with no other text.

Title: {title}
Summary: {summary}

Return this exact JSON structure:
{{
  "event_type": "<one of: technology, politics, business, science, other>",
  "sentiment": "<one of: positive, negative, neutral>",
  "importance": <integer 1-10>,
  "entities": ["<name>", "..."],
  "one_line_summary": "<max 20 words in English>",
  "signal_type": "<one of: funding_event, product_launch, leadership_change, market_expansion, partnership, hiring_growth, regulatory_risk, competitor_move, enterprise_adoption, security_incident, pricing_change, research_breakthrough, other>",
  "why_it_matters": "<one sentence explaining why this signal matters>",
  "business_implication": "<one sentence explaining the likely market or BD implication>",
  "suggested_action": "<one short action a BD, product, or market analysis team should take>",
  "target_persona": "<one of: founder, product_leader, sales_leader, marketing_leader, investor, analyst, engineer, policy_leader, other>",
  "urgency": <integer 1-10>
}}"""


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)


class AnalysisResult(BaseModel):
    event_type: Literal["technology", "politics", "business", "science", "other"]
    sentiment: Literal["positive", "negative", "neutral"]
    importance: int = Field(ge=1, le=10)
    entities: list[str] = Field(default_factory=list, max_length=20)
    one_line_summary: str = Field(min_length=3)
    signal_type: Literal[
        "funding_event",
        "product_launch",
        "leadership_change",
        "market_expansion",
        "partnership",
        "hiring_growth",
        "regulatory_risk",
        "competitor_move",
        "enterprise_adoption",
        "security_incident",
        "pricing_change",
        "research_breakthrough",
        "other",
    ]
    why_it_matters: str = Field(min_length=3)
    business_implication: str = Field(min_length=3)
    suggested_action: str = Field(min_length=3)
    target_persona: Literal[
        "founder",
        "product_leader",
        "sales_leader",
        "marketing_leader",
        "investor",
        "analyst",
        "engineer",
        "policy_leader",
        "other",
    ]
    urgency: int = Field(ge=1, le=10)

    @field_validator(
        "one_line_summary",
        "why_it_matters",
        "business_implication",
        "suggested_action",
        mode="before",
    )
    @classmethod
    def clean_text_fields(cls, value):
        if not isinstance(value, str):
            return value
        return " ".join(value.split()).strip()

    @field_validator("one_line_summary")
    @classmethod
    def enforce_summary_length(cls, value: str) -> str:
        words = value.split()
        return " ".join(words[:20])

    @field_validator("entities", mode="before")
    @classmethod
    def normalize_entities(cls, value):
        if not isinstance(value, list):
            return value

        normalized = []
        seen = set()
        for entity in value:
            if not isinstance(entity, str):
                continue
            clean_entity = " ".join(entity.split()).strip()
            key = clean_entity.casefold()
            if clean_entity and key not in seen:
                normalized.append(clean_entity)
                seen.add(key)
        return normalized[:20]


def get_supabase_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def get_claude_client():
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return " ".join(html.unescape(" ".join(parser.parts)).split())


def normalize_title(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def article_quality_issue(article: dict) -> str | None:
    title = normalize_title(article.get("title"))
    if not title:
        return "missing title"
    if len(title) < 8:
        return "title is too short"
    return None


def _contains_term(text: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.IGNORECASE) is not None


def relevance_score(title: str, summary: str) -> tuple[int, list[str]]:
    clean_title = normalize_title(title)
    clean_summary = strip_html(summary)
    score = 0
    matches: list[str] = []

    if re.search(r"(?<!\w)AI(?!\w)", clean_title):
        score += 3
        matches.append("AI (title)")
    elif re.search(r"(?<!\w)AI(?!\w)", clean_summary):
        score += 2
        matches.append("AI")

    for term in STRONG_AI_TERMS:
        if _contains_term(clean_title, term):
            score += 3
            matches.append(term)
        elif _contains_term(clean_summary, term):
            score += 2
            matches.append(term)

    for term in SUPPORTING_AI_TERMS:
        if _contains_term(clean_title, term):
            score += 1
            matches.append(term)
        elif _contains_term(clean_summary, term):
            score += 1
            matches.append(term)

    return score, list(dict.fromkeys(matches))


def extract_json_object(raw: str) -> dict:
    clean_raw = raw.strip()
    if clean_raw.startswith("```"):
        clean_raw = re.sub(r"^```(?:json)?\s*", "", clean_raw, flags=re.IGNORECASE)
        clean_raw = re.sub(r"\s*```$", "", clean_raw)

    start = clean_raw.find("{")
    end = clean_raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Claude response did not contain a JSON object")
    return json.loads(clean_raw[start : end + 1])


def analyze_article(
    title: str,
    summary: str,
    claude_client=None,
    max_attempts: int = MAX_ANALYSIS_ATTEMPTS,
) -> dict:
    client = claude_client or get_claude_client()
    prompt = PROMPT_TEMPLATE.format(
        title=normalize_title(title),
        summary=strip_html(summary)[:1000],
    )
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            message = client.messages.create(
                model=MODEL_NAME,
                max_tokens=700,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
            validated = AnalysisResult.model_validate(extract_json_object(raw))
            return validated.model_dump()
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts:
                time.sleep(0.5)

    raise RuntimeError(
        f"analysis failed after {max_attempts} attempt(s): {last_error}"
    ) from last_error


def fetch_analysis_batch(
    supabase_client,
    limit: int,
    min_relevance: int,
    include_low_relevance: bool = False,
    retry_failed: bool = False,
    reprocess_skipped: bool = False,
) -> tuple[list[dict], dict[str, int], list[dict]]:
    selected: list[dict] = []
    status_updates: list[dict] = []
    seen_titles: set[str] = set()
    stats = {
        "scanned": 0,
        "invalid": 0,
        "duplicate_title": 0,
        "low_relevance": 0,
    }
    offset = 0
    queue_statuses = ["pending"]
    if retry_failed:
        queue_statuses.append("failed")
    if reprocess_skipped:
        queue_statuses.append("skipped")

    while len(selected) < limit:
        result = (
            supabase_client.table("articles")
            .select("id, title, summary, analysis_attempts")
            .in_("analysis_status", queue_statuses)
            .order("id")
            .range(offset, offset + FETCH_PAGE_SIZE - 1)
            .execute()
        )
        page = result.data if isinstance(result.data, list) else []
        if not page:
            break

        for article in page:
            stats["scanned"] += 1
            issue = article_quality_issue(article)
            if issue:
                stats["invalid"] += 1
                status_updates.append(
                    {
                        "id": article["id"],
                        "analysis_status": "skipped",
                        "relevance_score": None,
                        "skip_reason": issue,
                    }
                )
                continue

            score, matches = relevance_score(
                article.get("title") or "",
                article.get("summary") or "",
            )
            if score < min_relevance and not include_low_relevance:
                stats["low_relevance"] += 1
                status_updates.append(
                    {
                        "id": article["id"],
                        "analysis_status": "skipped",
                        "relevance_score": score,
                        "skip_reason": (
                            f"AI relevance score {score} is below "
                            f"threshold {min_relevance}"
                        ),
                    }
                )
                continue

            title_key = normalize_title(article.get("title")).casefold()
            if title_key in seen_titles:
                stats["duplicate_title"] += 1
                status_updates.append(
                    {
                        "id": article["id"],
                        "analysis_status": "skipped",
                        "relevance_score": score,
                        "skip_reason": "duplicate normalized title in analysis queue",
                    }
                )
                continue
            seen_titles.add(title_key)

            selected.append(
                {
                    **article,
                    "_relevance_score": score,
                    "_relevance_matches": matches,
                }
            )
            if len(selected) >= limit:
                break

        if len(page) < FETCH_PAGE_SIZE:
            break
        offset += FETCH_PAGE_SIZE

    return selected, stats, status_updates


def persist_skip_decisions(supabase_client, status_updates: list[dict]) -> tuple[int, int]:
    saved = 0
    failed = 0
    attempted_at = datetime.now(timezone.utc).isoformat()

    for status_update in status_updates:
        article_id = status_update["id"]
        payload = {
            key: value
            for key, value in status_update.items()
            if key != "id"
        }
        payload.update(
            {
                "analysis_error": None,
                "analysis_attempted_at": attempted_at,
            }
        )
        try:
            (
                supabase_client.table("articles")
                .update(payload)
                .eq("id", article_id)
                .execute()
            )
            saved += 1
        except Exception as exc:
            failed += 1
            print(f"  Could not persist skip state for id={article_id}: {exc}")

    return saved, failed


def run_analysis(
    limit: int = DEFAULT_BATCH_LIMIT,
    min_relevance: int = DEFAULT_MIN_RELEVANCE,
    include_low_relevance: bool = False,
    retry_failed: bool = False,
    reprocess_skipped: bool = False,
    dry_run: bool = False,
):
    supabase_client = get_supabase_client()
    articles, stats, status_updates = fetch_analysis_batch(
        supabase_client=supabase_client,
        limit=limit,
        min_relevance=min_relevance,
        include_low_relevance=include_low_relevance,
        retry_failed=retry_failed,
        reprocess_skipped=reprocess_skipped,
    )
    total = len(articles)

    print(
        "\nSelection: "
        f"scanned={stats['scanned']}, selected={total}, "
        f"low_relevance={stats['low_relevance']}, "
        f"invalid={stats['invalid']}, "
        f"duplicate_title={stats['duplicate_title']}"
    )

    if dry_run:
        print(
            f"\nDry run: {total} article(s) would be analyzed and "
            f"{len(status_updates)} article(s) would be marked skipped; "
            "no API calls or writes.\n"
        )
        for i, article in enumerate(articles, 1):
            matches = ", ".join(article["_relevance_matches"][:4]) or "none"
            print(
                f"  [{i}/{total}] score={article['_relevance_score']} "
                f"| {article['title'][:70]} | matches: {matches}"
            )
        print()
        return

    saved_skips, failed_skips = persist_skip_decisions(
        supabase_client,
        status_updates,
    )
    if status_updates:
        print(
            f"Persisted skip decisions: saved={saved_skips}, "
            f"failed={failed_skips}"
        )

    if total == 0:
        print("No relevant queued articles found.\n")
        return

    claude_client = get_claude_client()
    print(
        f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Starting analysis for {total} article(s)\n"
    )

    success, failed = 0, 0
    for i, article in enumerate(articles, 1):
        article_id = article["id"]
        title = normalize_title(article.get("title"))
        summary = article.get("summary") or ""
        attempt_count = int(article.get("analysis_attempts") or 0) + 1
        attempted_at = datetime.now(timezone.utc).isoformat()

        try:
            analysis = analyze_article(
                title,
                summary,
                claude_client=claude_client,
            )
            supabase_client.table("articles").update(
                {
                    **analysis,
                    "analysis_status": "analyzed",
                    "relevance_score": article["_relevance_score"],
                    "skip_reason": None,
                    "analysis_attempts": attempt_count,
                    "analysis_error": None,
                    "analysis_attempted_at": attempted_at,
                    "analyzed_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", article_id).execute()

            success += 1
            print(
                f"  [{i}/{total}] OK id={article_id} "
                f"| relevance={article['_relevance_score']} | {title[:50]}"
            )
        except Exception as exc:
            failed += 1
            error_message = str(exc)[:1000]
            try:
                (
                    supabase_client.table("articles")
                    .update(
                        {
                            "analysis_status": "failed",
                            "relevance_score": article["_relevance_score"],
                            "skip_reason": None,
                            "analysis_attempts": attempt_count,
                            "analysis_error": error_message,
                            "analysis_attempted_at": attempted_at,
                        }
                    )
                    .eq("id", article_id)
                    .execute()
                )
            except Exception as status_exc:
                error_message = (
                    f"{error_message}; could not persist failed status: "
                    f"{status_exc}"
                )
            print(
                f"  [{i}/{total}] FAILED id={article_id} "
                f"| reason: {error_message}"
            )

        if i < total:
            time.sleep(0.5)

    print(f"\nCompleted: success={success}, failed={failed}\n")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be 0 or greater")
    return parsed


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze pending AI-industry articles in controlled batches."
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=DEFAULT_BATCH_LIMIT,
        help=f"maximum articles to analyze (default: {DEFAULT_BATCH_LIMIT})",
    )
    parser.add_argument(
        "--min-relevance",
        type=non_negative_int,
        default=DEFAULT_MIN_RELEVANCE,
        help=(
            "minimum local AI relevance score "
            f"(default: {DEFAULT_MIN_RELEVANCE})"
        ),
    )
    parser.add_argument(
        "--include-low-relevance",
        action="store_true",
        help="include articles below the local relevance threshold",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="include previously failed articles in this batch",
    )
    parser.add_argument(
        "--reprocess-skipped",
        action="store_true",
        help="re-evaluate previously skipped articles",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview the selected batch without Claude calls or database writes",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_analysis(
        limit=args.limit,
        min_relevance=args.min_relevance,
        include_low_relevance=args.include_low_relevance,
        retry_failed=args.retry_failed,
        reprocess_skipped=args.reprocess_skipped,
        dry_run=args.dry_run,
    )
