import argparse
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.analyzer.ai_analyzer import relevance_score, strip_html
from app.collector.rss_collector import (
    USER_AGENT,
    article_content_hash,
    canonicalize_url,
    fetch_existing_hashes,
    fetch_existing_urls,
    get_supabase_client,
    normalize_title,
    persist_articles,
)

TECHCRUNCH_API_URL = "https://techcrunch.com/wp-json/wp/v2/posts"
DEFAULT_SEARCH_QUERY = "artificial intelligence"
DEFAULT_TARGET = 1000
DEFAULT_PAGE_SIZE = 100
DEFAULT_MIN_RELEVANCE = 2
DEFAULT_REQUEST_DELAY = 2.0
REQUEST_TIMEOUT_SECONDS = 30
MAX_FETCH_ATTEMPTS = 3


@dataclass
class ArchivePageResult:
    page: int
    status: str = "ok"
    fetched: int = 0
    relevant: int = 0
    new_candidates: int = 0
    inserted: int = 0
    duplicates: int = 0
    invalid: int = 0
    write_failures: int = 0
    attempts: int = 0
    duration_seconds: float = 0.0
    error: str | None = None


@dataclass
class ArchiveSummary:
    results: list[ArchivePageResult]
    write: bool
    target: int

    @property
    def total_fetched(self) -> int:
        return sum(result.fetched for result in self.results)

    @property
    def total_relevant(self) -> int:
        return sum(result.relevant for result in self.results)

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
    def failed_pages(self) -> int:
        return sum(result.status == "failed" for result in self.results)


class ArchiveFetchError(RuntimeError):
    def __init__(self, message: str, attempts: int):
        super().__init__(message)
        self.attempts = attempts


def parse_wordpress_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def wordpress_post_to_record(post: dict) -> dict | None:
    raw_title = post.get("title", {})
    raw_excerpt = post.get("excerpt", {})
    title = normalize_title(
        strip_html(
            raw_title.get("rendered")
            if isinstance(raw_title, dict)
            else raw_title
        )
    )
    summary = strip_html(
        raw_excerpt.get("rendered")
        if isinstance(raw_excerpt, dict)
        else raw_excerpt
    )
    url = canonicalize_url(post.get("link"))
    published_at = parse_wordpress_datetime(
        post.get("date_gmt") or post.get("date")
    )
    if not title or not url or published_at is None:
        return None

    return {
        "title": title,
        "url": url,
        "source": "TechCrunch",
        "feed_url": TECHCRUNCH_API_URL,
        "published_at": published_at.isoformat(),
        "summary": summary[:2000] if summary else None,
        "content_hash": article_content_hash(title, published_at),
    }


def _retry_delay(response, attempt: int) -> float:
    retry_after = (
        response.headers.get("Retry-After")
        if response is not None
        else None
    )
    if retry_after:
        try:
            return max(float(retry_after), DEFAULT_REQUEST_DELAY)
        except ValueError:
            pass
    return DEFAULT_REQUEST_DELAY * attempt


def fetch_archive_page(
    http_client,
    page: int,
    page_size: int,
    search_query: str,
    max_attempts: int = MAX_FETCH_ATTEMPTS,
    sleep_fn=time.sleep,
) -> tuple[list[dict], int, int | None]:
    params = {
        "search": search_query,
        "per_page": page_size,
        "page": page,
        "orderby": "date",
        "order": "desc",
        "_fields": "date,date_gmt,link,title,excerpt",
    }
    last_error = None

    for attempt in range(1, max_attempts + 1):
        response = None
        try:
            response = http_client.get(TECHCRUNCH_API_URL, params=params)
            response.raise_for_status()
            posts = response.json()
            if not isinstance(posts, list):
                raise ValueError("WordPress response is not a list")
            total_pages_header = response.headers.get("X-WP-TotalPages")
            total_pages = (
                int(total_pages_header) if total_pages_header else None
            )
            return posts, attempt, total_pages
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            retryable = (
                response is None
                or response.status_code == 429
                or response.status_code >= 500
            )
            if attempt >= max_attempts or not retryable:
                break
            sleep_fn(_retry_delay(response, attempt))

    raise ArchiveFetchError(
        f"archive page {page} failed after {attempt} attempt(s): {last_error}",
        attempts=attempt,
    ) from last_error


def process_archive_page(
    supabase_client,
    http_client,
    page: int,
    page_size: int,
    search_query: str,
    min_relevance: int,
    remaining_target: int,
    write: bool,
    run_seen_hashes: set[str],
    run_seen_urls: set[str],
    sleep_fn=time.sleep,
) -> tuple[ArchivePageResult, int | None]:
    started_at = time.monotonic()
    result = ArchivePageResult(page=page)
    total_pages = None

    try:
        posts, result.attempts, total_pages = fetch_archive_page(
            http_client=http_client,
            page=page,
            page_size=page_size,
            search_query=search_query,
            sleep_fn=sleep_fn,
        )
        result.fetched = len(posts)

        candidates: list[dict] = []
        page_hashes: set[str] = set()
        page_urls: set[str] = set()
        for post in posts:
            record = wordpress_post_to_record(post)
            if record is None:
                result.invalid += 1
                continue

            score, _matches = relevance_score(
                record["title"],
                record["summary"] or "",
            )
            if score < min_relevance:
                continue
            result.relevant += 1

            content_hash = record["content_hash"]
            url = record["url"]
            if (
                content_hash in page_hashes
                or content_hash in run_seen_hashes
                or url in page_urls
                or url in run_seen_urls
            ):
                result.duplicates += 1
                continue
            page_hashes.add(content_hash)
            page_urls.add(url)
            candidates.append(record)

        existing_hashes = fetch_existing_hashes(
            supabase_client,
            [candidate["content_hash"] for candidate in candidates],
        )
        existing_urls = fetch_existing_urls(
            supabase_client,
            [candidate["url"] for candidate in candidates],
        )
        new_articles = []
        for candidate in candidates:
            if (
                candidate["content_hash"] in existing_hashes
                or candidate["url"] in existing_urls
            ):
                result.duplicates += 1
                run_seen_hashes.add(candidate["content_hash"])
                run_seen_urls.add(candidate["url"])
                continue
            new_articles.append(candidate)

        new_articles = new_articles[:remaining_target]
        result.new_candidates = len(new_articles)
        if write:
            (
                result.inserted,
                result.write_failures,
                ignored,
                persisted_hashes,
            ) = persist_articles(supabase_client, new_articles)
            result.duplicates += ignored
            run_seen_hashes.update(persisted_hashes)
            run_seen_urls.update(
                article["url"]
                for article in new_articles
                if article["content_hash"] in persisted_hashes
            )
            if result.write_failures:
                result.status = "partial"
        else:
            run_seen_hashes.update(
                article["content_hash"] for article in new_articles
            )
            run_seen_urls.update(article["url"] for article in new_articles)
    except Exception as exc:
        result.status = "failed"
        if isinstance(exc, ArchiveFetchError):
            result.attempts = exc.attempts
        result.error = str(exc)[:300]
    finally:
        result.duration_seconds = time.monotonic() - started_at

    return result, total_pages


def _target_progress(summary: ArchiveSummary) -> int:
    return (
        summary.total_inserted
        if summary.write
        else summary.total_new_candidates
    )


def print_archive_summary(summary: ArchiveSummary):
    value_label = "INSERTED" if summary.write else "WOULD_ADD"
    print(
        f"\n{'PAGE':>4} {'STATUS':8} {'FETCH':>5} {'REL':>5} "
        f"{value_label:>9} {'DUP':>5} {'TRY':>3} {'SEC':>6}"
    )
    print("-" * 62)
    for result in summary.results:
        value = result.inserted if summary.write else result.new_candidates
        print(
            f"{result.page:4d} {result.status:8} {result.fetched:5d} "
            f"{result.relevant:5d} {value:9d} {result.duplicates:5d} "
            f"{result.attempts:3d} {result.duration_seconds:6.1f}"
        )
        if result.error:
            print(f"  Error: {result.error}")
    print("-" * 62)
    print(
        f"Fetched={summary.total_fetched}, "
        f"relevant={summary.total_relevant}, "
        f"new_candidates={summary.total_new_candidates}, "
        f"inserted={summary.total_inserted}, "
        f"duplicates={summary.total_duplicates}, "
        f"failed_pages={summary.failed_pages}"
    )
    if not summary.write:
        print("Preview complete: no articles were written.")
    print()


def run_archive_backfill(
    target: int = DEFAULT_TARGET,
    page_size: int = DEFAULT_PAGE_SIZE,
    min_relevance: int = DEFAULT_MIN_RELEVANCE,
    request_delay: float = DEFAULT_REQUEST_DELAY,
    search_query: str = DEFAULT_SEARCH_QUERY,
    write: bool = False,
    max_pages: int | None = None,
    supabase_client=None,
    http_client=None,
    sleep_fn=time.sleep,
) -> ArchiveSummary:
    database = supabase_client or get_supabase_client()
    owns_http_client = http_client is None
    web_client = http_client or httpx.Client(
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    summary = ArchiveSummary(results=[], write=write, target=target)
    run_seen_hashes: set[str] = set()
    run_seen_urls: set[str] = set()
    total_pages = None

    print(
        f"\nStarting TechCrunch archive backfill: query={search_query!r}, "
        f"target={target}, write={write}"
    )

    try:
        page = 1
        while _target_progress(summary) < target:
            if max_pages is not None and page > max_pages:
                break
            if total_pages is not None and page > total_pages:
                break
            if summary.results and request_delay:
                sleep_fn(request_delay)

            progress = _target_progress(summary)
            result, reported_total_pages = process_archive_page(
                supabase_client=database,
                http_client=web_client,
                page=page,
                page_size=page_size,
                search_query=search_query,
                min_relevance=min_relevance,
                remaining_target=target - progress,
                write=write,
                run_seen_hashes=run_seen_hashes,
                run_seen_urls=run_seen_urls,
                sleep_fn=sleep_fn,
            )
            summary.results.append(result)
            if reported_total_pages is not None:
                total_pages = reported_total_pages
            value = result.inserted if write else result.new_candidates
            print(
                f"  page={page}: status={result.status}, "
                f"fetched={result.fetched}, relevant={result.relevant}, "
                f"new={value}, duplicates={result.duplicates}"
            )
            if result.status == "failed" or result.fetched == 0:
                break
            page += 1
    finally:
        if owns_http_client:
            web_client.close()

    print_archive_summary(summary)
    return summary


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def page_size_int(value: str) -> int:
    parsed = positive_int(value)
    if parsed > 100:
        raise argparse.ArgumentTypeError(
            "must not exceed WordPress's limit of 100"
        )
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be 0 or greater")
    return parsed


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Preview or import historical AI-industry news from the "
            "TechCrunch public WordPress archive."
        )
    )
    parser.add_argument(
        "--target",
        type=positive_int,
        default=DEFAULT_TARGET,
        help=f"stop after this many new rows (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--page-size",
        type=page_size_int,
        default=DEFAULT_PAGE_SIZE,
        help=f"archive rows per request (default: {DEFAULT_PAGE_SIZE})",
    )
    parser.add_argument(
        "--min-relevance",
        type=positive_int,
        default=DEFAULT_MIN_RELEVANCE,
        help=(
            "minimum local title/summary relevance score "
            f"(default: {DEFAULT_MIN_RELEVANCE})"
        ),
    )
    parser.add_argument(
        "--request-delay",
        type=non_negative_float,
        default=DEFAULT_REQUEST_DELAY,
        help=(
            "seconds between archive pages "
            f"(default: {DEFAULT_REQUEST_DELAY})"
        ),
    )
    parser.add_argument(
        "--max-pages",
        type=positive_int,
        help="optional safety cap for the number of archive pages",
    )
    parser.add_argument(
        "--search",
        default=DEFAULT_SEARCH_QUERY,
        help=f"archive search phrase (default: {DEFAULT_SEARCH_QUERY!r})",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write new rows; without this flag the command is preview-only",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    summary = run_archive_backfill(
        target=args.target,
        page_size=args.page_size,
        min_relevance=args.min_relevance,
        request_delay=args.request_delay,
        search_query=args.search,
        write=args.write,
        max_pages=args.max_pages,
    )
    if summary.failed_pages or any(
        result.write_failures for result in summary.results
    ):
        raise SystemExit(1)
