import argparse
import hashlib
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

REQUEST_TIMEOUT_SECONDS = 15
MAX_FETCH_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0
HASH_QUERY_BATCH_SIZE = 50
WRITE_BATCH_SIZE = 50
USER_AGENT = (
    "NestorInsight/0.2 "
    "(AI industry signal research; "
    "+https://github.com/pan00051/project-nestor-insight)"
)

RSS_FEEDS = [
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml"},
    {"name": "Wired", "url": "https://www.wired.com/feed/rss"},
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/"},
    {"name": "VentureBeat", "url": "https://venturebeat.com/feed/"},
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"name": "TechRepublic", "url": "https://www.techrepublic.com/rssfeeds/articles/"},
    {"name": "ZDNet", "url": "https://www.zdnet.com/news/rss.xml"},
    {"name": "InfoQ", "url": "https://feed.infoq.com"},
]


@dataclass
class FeedCollectionResult:
    source: str
    status: str = "ok"
    fetched: int = 0
    new_candidates: int = 0
    inserted: int = 0
    duplicates: int = 0
    invalid: int = 0
    write_failures: int = 0
    attempts: int = 0
    duration_seconds: float = 0.0
    error: str | None = None


@dataclass
class CollectionSummary:
    results: list[FeedCollectionResult]
    dry_run: bool

    @property
    def total_fetched(self) -> int:
        return sum(result.fetched for result in self.results)

    @property
    def total_new_candidates(self) -> int:
        return sum(result.new_candidates for result in self.results)

    @property
    def total_inserted(self) -> int:
        return sum(result.inserted for result in self.results)

    @property
    def total_duplicates(self) -> int:
        return sum(result.duplicates for result in self.results)

    @property
    def failed_sources(self) -> int:
        return sum(result.status == "failed" for result in self.results)


class FeedFetchError(RuntimeError):
    def __init__(self, message: str, attempts: int = 0):
        super().__init__(message)
        self.attempts = attempts


def get_supabase_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def normalize_title(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def article_content_hash(title: str, published_at: datetime | None) -> str:
    published_date = (
        normalize_datetime(published_at).date().isoformat()
        if published_at is not None
        else ""
    )
    hash_input = f"{normalize_title(title).lower()}|{published_date}"
    return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()


def parse_published_at(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed_time = getattr(entry, attr, None)
        if parsed_time:
            try:
                return datetime(*parsed_time[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated"):
        raw_time = getattr(entry, attr, None)
        if raw_time:
            try:
                return normalize_datetime(parsedate_to_datetime(raw_time))
            except Exception:
                pass
    return None


def chunked(values: list, size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def fetch_feed(
    feed_url: str,
    http_client,
    max_attempts: int = MAX_FETCH_ATTEMPTS,
    sleep_fn=time.sleep,
):
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = http_client.get(feed_url)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            if not parsed.entries:
                parse_error = (
                    str(parsed.bozo_exception)
                    if parsed.bozo
                    else "feed returned no entries"
                )
                raise FeedFetchError(f"RSS parse failed: {parse_error}")
            return parsed, attempt
        except (httpx.HTTPError, FeedFetchError) as exc:
            last_error = exc
            if attempt < max_attempts:
                sleep_fn(RETRY_BACKOFF_SECONDS * attempt)

    raise FeedFetchError(
        f"feed failed after {max_attempts} attempt(s): {last_error}",
        attempts=max_attempts,
    ) from last_error


def build_article(entry, feed_name: str, feed_url: str) -> dict | None:
    url = (entry.get("link") or "").strip()
    title = normalize_title(entry.get("title"))
    if not url or not title:
        return None

    summary = entry.get("summary", "") or entry.get("description", "")
    parsed_published_at = parse_published_at(entry)
    stored_published_at = parsed_published_at or datetime.now(timezone.utc)

    return {
        "title": title,
        "url": url,
        "source": feed_name,
        "feed_url": feed_url,
        "published_at": normalize_datetime(stored_published_at).isoformat(),
        "summary": summary[:2000] if summary else None,
        "content_hash": article_content_hash(title, parsed_published_at),
    }


def fetch_existing_hashes(supabase_client, content_hashes: list[str]) -> set[str]:
    return fetch_existing_values(
        supabase_client,
        field="content_hash",
        values=content_hashes,
    )


def fetch_existing_urls(supabase_client, urls: list[str]) -> set[str]:
    return fetch_existing_values(
        supabase_client,
        field="url",
        values=urls,
    )


def fetch_existing_values(
    supabase_client,
    field: str,
    values: list[str],
) -> set[str]:
    existing: set[str] = set()
    unique_values = list(dict.fromkeys(values))

    for value_batch in chunked(unique_values, HASH_QUERY_BATCH_SIZE):
        result = (
            supabase_client.table("articles")
            .select(field)
            .in_(field, value_batch)
            .execute()
        )
        rows = result.data if isinstance(result.data, list) else []
        existing.update(
            row[field]
            for row in rows
            if row.get(field)
        )

    return existing


def persist_articles(
    supabase_client,
    articles: list[dict],
) -> tuple[int, int, int, set[str]]:
    inserted = 0
    failed = 0
    persisted_hashes: set[str] = set()

    for article_batch in chunked(articles, WRITE_BATCH_SIZE):
        try:
            result = (
                supabase_client.table("articles")
                .upsert(
                    article_batch,
                    on_conflict="url",
                    ignore_duplicates=True,
                )
                .execute()
            )
            rows = result.data if isinstance(result.data, list) else []
            inserted += len(rows)
            persisted_hashes.update(
                row["content_hash"]
                for row in rows
                if row.get("content_hash")
            )
        except Exception as batch_error:
            print(f"    Batch write failed; isolating rows: {batch_error}")
            for article in article_batch:
                try:
                    result = (
                        supabase_client.table("articles")
                        .upsert(
                            article,
                            on_conflict="url",
                            ignore_duplicates=True,
                        )
                        .execute()
                    )
                    rows = result.data if isinstance(result.data, list) else []
                    inserted += len(rows)
                    persisted_hashes.update(
                        row["content_hash"]
                        for row in rows
                        if row.get("content_hash")
                    )
                except Exception as row_error:
                    failed += 1
                    print(
                        f"    Write failed: {article['url'][:70]} "
                        f"| {row_error}"
                    )

    ignored = max(0, len(articles) - inserted - failed)
    return inserted, failed, ignored, persisted_hashes


def collect_feed(
    feed_name: str,
    feed_url: str,
    supabase_client,
    http_client,
    run_seen_hashes: set[str],
    max_per_feed: int | None = None,
    dry_run: bool = False,
    max_attempts: int = MAX_FETCH_ATTEMPTS,
    sleep_fn=time.sleep,
) -> FeedCollectionResult:
    started_at = time.monotonic()
    result = FeedCollectionResult(source=feed_name)

    try:
        parsed, result.attempts = fetch_feed(
            feed_url,
            http_client=http_client,
            max_attempts=max_attempts,
            sleep_fn=sleep_fn,
        )
        entries = list(parsed.entries)
        if max_per_feed is not None:
            entries = entries[:max_per_feed]
        result.fetched = len(entries)

        candidates: list[dict] = []
        feed_hashes: set[str] = set()
        for entry in entries:
            article = build_article(entry, feed_name, feed_url)
            if article is None:
                result.invalid += 1
                continue

            content_hash = article["content_hash"]
            if content_hash in feed_hashes or content_hash in run_seen_hashes:
                result.duplicates += 1
                continue
            feed_hashes.add(content_hash)
            candidates.append(article)

        existing_hashes = fetch_existing_hashes(
            supabase_client,
            [article["content_hash"] for article in candidates],
        )
        existing_urls = fetch_existing_urls(
            supabase_client,
            [article["url"] for article in candidates],
        )
        new_articles = []
        for article in candidates:
            content_hash = article["content_hash"]
            if (
                content_hash in existing_hashes
                or article["url"] in existing_urls
            ):
                result.duplicates += 1
                run_seen_hashes.add(content_hash)
                continue
            new_articles.append(article)

        result.new_candidates = len(new_articles)
        if dry_run:
            run_seen_hashes.update(
                article["content_hash"] for article in new_articles
            )
        else:
            (
                result.inserted,
                result.write_failures,
                write_ignored,
                persisted_hashes,
            ) = persist_articles(supabase_client, new_articles)
            result.duplicates += write_ignored
            run_seen_hashes.update(persisted_hashes)

        if result.write_failures:
            result.status = "partial"
    except Exception as exc:
        result.status = "failed"
        if isinstance(exc, FeedFetchError):
            result.attempts = exc.attempts
        result.error = str(exc)[:300]
    finally:
        result.duration_seconds = time.monotonic() - started_at

    return result


def select_feeds(source_names: list[str] | None) -> list[dict]:
    if not source_names:
        return RSS_FEEDS

    by_name = {feed["name"].casefold(): feed for feed in RSS_FEEDS}
    selected = []
    unknown = []
    for source_name in source_names:
        feed = by_name.get(source_name.casefold())
        if feed is None:
            unknown.append(source_name)
        elif feed not in selected:
            selected.append(feed)

    if unknown:
        choices = ", ".join(feed["name"] for feed in RSS_FEEDS)
        raise ValueError(
            f"Unknown source(s): {', '.join(unknown)}. Available: {choices}"
        )
    return selected


def print_summary(summary: CollectionSummary):
    value_label = "WOULD_ADD" if summary.dry_run else "INSERTED"
    print(
        f"\n{'SOURCE':25} {'STATUS':8} {'FETCHED':>7} "
        f"{value_label:>9} {'DUPES':>7} {'INVALID':>7} "
        f"{'TRY':>3} {'SEC':>6}"
    )
    print("-" * 83)
    for result in summary.results:
        new_value = (
            result.new_candidates if summary.dry_run else result.inserted
        )
        print(
            f"{result.source[:25]:25} {result.status:8} "
            f"{result.fetched:7d} {new_value:9d} "
            f"{result.duplicates:7d} {result.invalid:7d} "
            f"{result.attempts:3d} {result.duration_seconds:6.1f}"
        )
        if result.error:
            print(f"  Error: {result.error}")

    print("-" * 83)
    print(
        f"Fetched={summary.total_fetched}, "
        f"new_candidates={summary.total_new_candidates}, "
        f"inserted={summary.total_inserted}, "
        f"duplicates={summary.total_duplicates}, "
        f"failed_sources={summary.failed_sources}"
    )
    if summary.dry_run:
        print("Dry run complete: no articles were written.")
    print()


def run_collection(
    feeds: list[dict] | None = None,
    max_per_feed: int | None = None,
    dry_run: bool = False,
    supabase_client=None,
    http_client=None,
) -> CollectionSummary:
    selected_feeds = feeds or RSS_FEEDS
    database = supabase_client or get_supabase_client()
    owns_http_client = http_client is None
    web_client = http_client or httpx.Client(
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/rss+xml, application/atom+xml, "
                "application/xml, text/xml;q=0.9, */*;q=0.8"
            ),
        },
    )

    print(
        f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Starting collection: sources={len(selected_feeds)}, "
        f"dry_run={dry_run}"
    )
    run_seen_hashes: set[str] = set()
    results = []

    try:
        for feed in selected_feeds:
            result = collect_feed(
                feed["name"],
                feed["url"],
                supabase_client=database,
                http_client=web_client,
                run_seen_hashes=run_seen_hashes,
                max_per_feed=max_per_feed,
                dry_run=dry_run,
            )
            results.append(result)
            print(
                f"  {feed['name']}: status={result.status}, "
                f"fetched={result.fetched}, "
                f"new={result.new_candidates}, "
                f"duplicates={result.duplicates}"
            )
    finally:
        if owns_http_client:
            web_client.close()

    summary = CollectionSummary(results=results, dry_run=dry_run)
    print_summary(summary)
    return summary


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect AI-industry RSS articles with retries and deduplication."
    )
    parser.add_argument(
        "--source",
        action="append",
        help="collect one exact source name; repeat to select multiple sources",
    )
    parser.add_argument(
        "--max-per-feed",
        type=positive_int,
        help="limit how many entries are inspected from each selected feed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and deduplicate without writing articles",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        chosen_feeds = select_feeds(args.source)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    run_collection(
        feeds=chosen_feeds,
        max_per_feed=args.max_per_feed,
        dry_run=args.dry_run,
    )
