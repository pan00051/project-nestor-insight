import argparse
import time
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from app.analyzer.ai_analyzer import relevance_score
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

GDELT_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_QUERY = (
    '("artificial intelligence" OR "generative AI" OR OpenAI OR Anthropic '
    'OR ChatGPT OR Claude OR "large language model" OR "AI agent" '
    "OR Nvidia) sourcelang:english"
)
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_WINDOW_DAYS = 7
DEFAULT_MAX_RECORDS = 250
DEFAULT_TARGET = 1000
DEFAULT_MIN_RELEVANCE = 3
DEFAULT_REQUEST_DELAY = 6.0
REQUEST_TIMEOUT_SECONDS = 30
MAX_FETCH_ATTEMPTS = 4


@dataclass
class BackfillWindowResult:
    start: datetime
    end: datetime
    status: str = "ok"
    fetched: int = 0
    relevant: int = 0
    new_candidates: int = 0
    inserted: int = 0
    duplicates: int = 0
    invalid: int = 0
    write_failures: int = 0
    attempts: int = 0
    saturated: bool = False
    duration_seconds: float = 0.0
    error: str | None = None


@dataclass
class BackfillSummary:
    results: list[BackfillWindowResult]
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
    def saturated_windows(self) -> int:
        return sum(result.saturated for result in self.results)

    @property
    def failed_windows(self) -> int:
        return sum(result.status == "failed" for result in self.results)


class GdeltFetchError(RuntimeError):
    def __init__(self, message: str, attempts: int):
        super().__init__(message)
        self.attempts = attempts


def utc_start(day: date) -> datetime:
    return datetime.combine(day, datetime_time.min, tzinfo=timezone.utc)


def iter_date_windows(
    start_date: date,
    end_date: date,
    window_days: int,
):
    overall_start = utc_start(start_date)
    cursor_end = utc_start(end_date + timedelta(days=1))

    while cursor_end > overall_start:
        window_start = max(
            overall_start,
            cursor_end - timedelta(days=window_days),
        )
        window_end = cursor_end - timedelta(seconds=1)
        yield window_start, window_end
        cursor_end = window_start


def parse_gdelt_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def gdelt_article_to_record(article: dict) -> dict | None:
    title = normalize_title(article.get("title"))
    url = canonicalize_url(article.get("url"))
    published_at = parse_gdelt_datetime(article.get("seendate"))
    if not title or not url or published_at is None:
        return None

    domain = (article.get("domain") or urlsplit(url).netloc).strip().lower()
    return {
        "title": title,
        "url": url,
        "source": domain or "GDELT",
        "feed_url": GDELT_API_URL,
        "published_at": published_at.isoformat(),
        "summary": None,
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


def fetch_gdelt_window(
    http_client,
    query: str,
    start: datetime,
    end: datetime,
    max_records: int,
    max_attempts: int = MAX_FETCH_ATTEMPTS,
    sleep_fn=time.sleep,
) -> tuple[list[dict], int]:
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": max_records,
        "format": "json",
        "sort": "datedesc",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
    }
    last_error = None

    for attempt in range(1, max_attempts + 1):
        response = None
        try:
            response = http_client.get(GDELT_API_URL, params=params)
            response.raise_for_status()
            payload = response.json()
            articles = payload.get("articles", [])
            if not isinstance(articles, list):
                raise ValueError("GDELT response field 'articles' is not a list")
            return articles, attempt
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

    raise GdeltFetchError(
        f"GDELT window failed after {attempt} attempt(s): {last_error}",
        attempts=attempt,
    ) from last_error


def _target_progress(summary: BackfillSummary) -> int:
    return (
        summary.total_inserted
        if summary.write
        else summary.total_new_candidates
    )


def process_window(
    supabase_client,
    http_client,
    query: str,
    start: datetime,
    end: datetime,
    max_records: int,
    min_relevance: int,
    remaining_target: int,
    write: bool,
    run_seen_hashes: set[str],
    run_seen_urls: set[str],
    sleep_fn=time.sleep,
) -> BackfillWindowResult:
    started_at = time.monotonic()
    result = BackfillWindowResult(start=start, end=end)

    try:
        articles, result.attempts = fetch_gdelt_window(
            http_client=http_client,
            query=query,
            start=start,
            end=end,
            max_records=max_records,
            sleep_fn=sleep_fn,
        )
        result.fetched = len(articles)
        result.saturated = len(articles) >= max_records

        candidates: list[dict] = []
        window_hashes: set[str] = set()
        window_urls: set[str] = set()
        for gdelt_article in articles:
            record = gdelt_article_to_record(gdelt_article)
            if record is None:
                result.invalid += 1
                continue

            score, _matches = relevance_score(record["title"], "")
            if score < min_relevance:
                continue
            result.relevant += 1

            content_hash = record["content_hash"]
            url = record["url"]
            if (
                content_hash in window_hashes
                or content_hash in run_seen_hashes
                or url in window_urls
                or url in run_seen_urls
            ):
                result.duplicates += 1
                continue
            window_hashes.add(content_hash)
            window_urls.add(url)
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
        if isinstance(exc, GdeltFetchError):
            result.attempts = exc.attempts
        result.error = str(exc)[:300]
    finally:
        result.duration_seconds = time.monotonic() - started_at

    return result


def print_backfill_summary(summary: BackfillSummary):
    value_label = "INSERTED" if summary.write else "WOULD_ADD"
    print(
        f"\n{'WINDOW':23} {'STATUS':8} {'FETCH':>5} {'REL':>5} "
        f"{value_label:>9} {'DUP':>5} {'TRY':>3} {'SAT':>3} {'SEC':>6}"
    )
    print("-" * 82)
    for result in summary.results:
        window_label = (
            f"{result.start:%Y-%m-%d}..{result.end:%Y-%m-%d}"
        )
        value = result.inserted if summary.write else result.new_candidates
        print(
            f"{window_label:23} {result.status:8} "
            f"{result.fetched:5d} {result.relevant:5d} {value:9d} "
            f"{result.duplicates:5d} {result.attempts:3d} "
            f"{'yes' if result.saturated else 'no':>3} "
            f"{result.duration_seconds:6.1f}"
        )
        if result.error:
            print(f"  Error: {result.error}")

    print("-" * 82)
    print(
        f"Fetched={summary.total_fetched}, "
        f"relevant={summary.total_relevant}, "
        f"new_candidates={summary.total_new_candidates}, "
        f"inserted={summary.total_inserted}, "
        f"duplicates={summary.total_duplicates}, "
        f"saturated_windows={summary.saturated_windows}, "
        f"failed_windows={summary.failed_windows}"
    )
    if not summary.write:
        print("Preview complete: no articles were written.")
    if summary.saturated_windows:
        print(
            "Warning: saturated windows reached the GDELT result cap. "
            "Use a smaller --window-days value for more complete coverage."
        )
    print()


def run_backfill(
    start_date: date,
    end_date: date,
    target: int = DEFAULT_TARGET,
    window_days: int = DEFAULT_WINDOW_DAYS,
    max_records: int = DEFAULT_MAX_RECORDS,
    min_relevance: int = DEFAULT_MIN_RELEVANCE,
    request_delay: float = DEFAULT_REQUEST_DELAY,
    query: str = DEFAULT_QUERY,
    write: bool = False,
    max_windows: int | None = None,
    supabase_client=None,
    http_client=None,
    sleep_fn=time.sleep,
) -> BackfillSummary:
    if end_date < start_date:
        raise ValueError("end date must be on or after start date")

    database = supabase_client or get_supabase_client()
    owns_http_client = http_client is None
    web_client = http_client or httpx.Client(
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    summary = BackfillSummary(results=[], write=write, target=target)
    run_seen_hashes: set[str] = set()
    run_seen_urls: set[str] = set()

    print(
        f"\nStarting GDELT backfill: {start_date}..{end_date}, "
        f"target={target}, window_days={window_days}, write={write}"
    )

    try:
        for window_index, (window_start, window_end) in enumerate(
            iter_date_windows(start_date, end_date, window_days),
            1,
        ):
            if max_windows is not None and window_index > max_windows:
                break
            progress = _target_progress(summary)
            if progress >= target:
                break
            if summary.results and request_delay:
                sleep_fn(request_delay)

            result = process_window(
                supabase_client=database,
                http_client=web_client,
                query=query,
                start=window_start,
                end=window_end,
                max_records=max_records,
                min_relevance=min_relevance,
                remaining_target=target - progress,
                write=write,
                run_seen_hashes=run_seen_hashes,
                run_seen_urls=run_seen_urls,
                sleep_fn=sleep_fn,
            )
            summary.results.append(result)
            value = result.inserted if write else result.new_candidates
            print(
                f"  {window_start:%Y-%m-%d}..{window_end:%Y-%m-%d}: "
                f"status={result.status}, fetched={result.fetched}, "
                f"relevant={result.relevant}, new={value}, "
                f"duplicates={result.duplicates}"
            )
    finally:
        if owns_http_client:
            web_client.close()

    print_backfill_summary(summary)
    return summary


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def max_records_int(value: str) -> int:
    parsed = positive_int(value)
    if parsed > 250:
        raise argparse.ArgumentTypeError("must not exceed GDELT's limit of 250")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be 0 or greater")
    return parsed


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD") from exc


def parse_args():
    today = datetime.now(timezone.utc).date()
    parser = argparse.ArgumentParser(
        description=(
            "Preview or import historical AI-industry news from the "
            "GDELT DOC API."
        )
    )
    parser.add_argument(
        "--start",
        type=iso_date,
        default=today - timedelta(days=DEFAULT_LOOKBACK_DAYS),
        help="inclusive start date in YYYY-MM-DD (default: 90 days ago)",
    )
    parser.add_argument(
        "--end",
        type=iso_date,
        default=today,
        help="inclusive end date in YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--target",
        type=positive_int,
        default=DEFAULT_TARGET,
        help=f"stop after this many new rows (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--window-days",
        type=positive_int,
        default=DEFAULT_WINDOW_DAYS,
        help=f"days per API query (default: {DEFAULT_WINDOW_DAYS})",
    )
    parser.add_argument(
        "--max-records",
        type=max_records_int,
        default=DEFAULT_MAX_RECORDS,
        help=f"GDELT rows per query, max 250 (default: {DEFAULT_MAX_RECORDS})",
    )
    parser.add_argument(
        "--min-relevance",
        type=positive_int,
        default=DEFAULT_MIN_RELEVANCE,
        help=(
            "minimum local title relevance score "
            f"(default: {DEFAULT_MIN_RELEVANCE})"
        ),
    )
    parser.add_argument(
        "--request-delay",
        type=non_negative_float,
        default=DEFAULT_REQUEST_DELAY,
        help=(
            "seconds between GDELT windows "
            f"(default: {DEFAULT_REQUEST_DELAY})"
        ),
    )
    parser.add_argument(
        "--max-windows",
        type=positive_int,
        help="optional safety cap for the number of date windows",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help="custom GDELT query",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write new rows; without this flag the command is preview-only",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    summary = run_backfill(
        start_date=args.start,
        end_date=args.end,
        target=args.target,
        window_days=args.window_days,
        max_records=args.max_records,
        min_relevance=args.min_relevance,
        request_delay=args.request_delay,
        query=args.query,
        write=args.write,
        max_windows=args.max_windows,
    )
    if summary.failed_windows or any(
        result.write_failures for result in summary.results
    ):
        raise SystemExit(1)
