import unittest
from datetime import datetime, timedelta, timezone

import httpx

from app.collector.rss_collector import (
    FeedFetchError,
    article_content_hash,
    collect_feed,
    fetch_feed,
    select_feeds,
)


RSS_XML = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test feed</title>
    <item>
      <title>OpenAI launches a new enterprise model</title>
      <link>https://example.com/openai-1</link>
      <pubDate>Thu, 23 Jul 2026 10:00:00 GMT</pubDate>
      <description>First copy.</description>
    </item>
    <item>
      <title>OpenAI launches a new enterprise model</title>
      <link>https://another.example.com/openai-1</link>
      <pubDate>Thu, 23 Jul 2026 10:00:00 GMT</pubDate>
      <description>Syndicated copy.</description>
    </item>
    <item>
      <title>Anthropic expands Claude for enterprise teams</title>
      <link>https://example.com/anthropic-1</link>
      <pubDate>Thu, 23 Jul 2026 12:00:00 GMT</pubDate>
      <description>Existing article.</description>
    </item>
  </channel>
</rss>
"""


class FakeHttpResponse:
    def __init__(self, content=RSS_XML, error=None):
        self.content = content
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error


class FakeHttpClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, _url):
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeCollectorQuery:
    def __init__(self, existing_hashes=None, existing_urls=None):
        self.existing_values = {
            "content_hash": set(existing_hashes or []),
            "url": set(existing_urls or []),
        }
        self.selected_field = None
        self.requested_values = []
        self.pending_articles = []
        self.mode = "select"
        self.upsert_calls = 0

    def select(self, field):
        self.mode = "select"
        self.selected_field = field
        return self

    def in_(self, field, values):
        self.selected_field = field
        self.requested_values = list(values)
        return self

    def upsert(self, articles, **_kwargs):
        self.mode = "upsert"
        self.upsert_calls += 1
        self.pending_articles = articles if isinstance(articles, list) else [articles]
        return self

    def execute(self):
        if self.mode == "select":
            return type(
                "Result",
                (),
                {
                    "data": [
                        {self.selected_field: value}
                        for value in self.requested_values
                        if value in self.existing_values[self.selected_field]
                    ]
                },
            )()
        return type("Result", (), {"data": self.pending_articles})()


class FakeCollectorSupabase:
    def __init__(self, existing_hashes=None, existing_urls=None):
        self.query = FakeCollectorQuery(existing_hashes, existing_urls)

    def table(self, _name):
        return self.query


class CollectorIdentityTests(unittest.TestCase):
    def test_hash_normalizes_title_whitespace_and_case(self):
        published_at = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
        first = article_content_hash(
            " OpenAI   Launches a New Model ",
            published_at,
        )
        second = article_content_hash(
            "openai launches a new model",
            published_at,
        )
        self.assertEqual(first, second)

    def test_hash_normalizes_publication_date_to_utc(self):
        utc_time = datetime(2026, 7, 23, 23, 30, tzinfo=timezone.utc)
        offset_time = datetime(
            2026,
            7,
            24,
            1,
            30,
            tzinfo=timezone(timedelta(hours=2)),
        )
        self.assertEqual(
            article_content_hash("Anthropic update", utc_time),
            article_content_hash("Anthropic update", offset_time),
        )

    def test_hash_changes_on_a_different_publication_date(self):
        first_day = datetime(2026, 7, 23, tzinfo=timezone.utc)
        second_day = datetime(2026, 7, 24, tzinfo=timezone.utc)
        self.assertNotEqual(
            article_content_hash("Weekly AI update", first_day),
            article_content_hash("Weekly AI update", second_day),
        )

    def test_hash_without_publication_date_is_stable(self):
        self.assertEqual(
            article_content_hash("AI policy update", None),
            article_content_hash("AI policy update", None),
        )

    def test_fetch_feed_retries_transient_http_error(self):
        request = httpx.Request("GET", "https://example.com/feed")
        client = FakeHttpClient(
            [
                httpx.ConnectError("temporary failure", request=request),
                FakeHttpResponse(),
            ]
        )
        sleeps = []

        parsed, attempts = fetch_feed(
            "https://example.com/feed",
            http_client=client,
            max_attempts=3,
            sleep_fn=sleeps.append,
        )

        self.assertEqual(attempts, 2)
        self.assertEqual(len(parsed.entries), 3)
        self.assertEqual(sleeps, [1.0])

    def test_fetch_feed_reports_all_failed_attempts(self):
        request = httpx.Request("GET", "https://example.com/feed")
        failures = [
            httpx.ConnectError("temporary failure", request=request)
            for _ in range(3)
        ]

        with self.assertRaises(FeedFetchError) as context:
            fetch_feed(
                "https://example.com/feed",
                http_client=FakeHttpClient(failures),
                max_attempts=3,
                sleep_fn=lambda _seconds: None,
            )

        self.assertEqual(context.exception.attempts, 3)

    def test_fetch_feed_rejects_empty_response(self):
        with self.assertRaises(FeedFetchError):
            fetch_feed(
                "https://example.com/feed",
                http_client=FakeHttpClient(
                    [FakeHttpResponse(content=b"<rss><channel /></rss>")]
                ),
                max_attempts=1,
                sleep_fn=lambda _seconds: None,
            )

    def test_collect_feed_batches_deduplication_in_dry_run(self):
        existing_hash = article_content_hash(
            "Anthropic expands Claude for enterprise teams",
            datetime(2026, 7, 23, 12, tzinfo=timezone.utc),
        )
        database = FakeCollectorSupabase({existing_hash})
        run_seen_hashes = set()

        result = collect_feed(
            "Test Source",
            "https://example.com/feed",
            supabase_client=database,
            http_client=FakeHttpClient([FakeHttpResponse()]),
            run_seen_hashes=run_seen_hashes,
            dry_run=True,
            sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.fetched, 3)
        self.assertEqual(result.new_candidates, 1)
        self.assertEqual(result.duplicates, 2)
        self.assertEqual(result.inserted, 0)
        self.assertEqual(database.query.upsert_calls, 0)
        self.assertEqual(len(run_seen_hashes), 2)

    def test_collect_feed_writes_new_articles_in_one_batch(self):
        database = FakeCollectorSupabase()
        run_seen_hashes = set()

        result = collect_feed(
            "Test Source",
            "https://example.com/feed",
            supabase_client=database,
            http_client=FakeHttpClient([FakeHttpResponse()]),
            run_seen_hashes=run_seen_hashes,
            max_per_feed=1,
            dry_run=False,
            sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(result.new_candidates, 1)
        self.assertEqual(result.inserted, 1)
        self.assertEqual(database.query.upsert_calls, 1)
        self.assertEqual(len(run_seen_hashes), 1)

    def test_collect_feed_treats_existing_url_as_duplicate(self):
        database = FakeCollectorSupabase(
            existing_urls={"https://example.com/openai-1"}
        )

        result = collect_feed(
            "Test Source",
            "https://example.com/feed",
            supabase_client=database,
            http_client=FakeHttpClient([FakeHttpResponse()]),
            run_seen_hashes=set(),
            max_per_feed=1,
            dry_run=True,
            sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(result.new_candidates, 0)
        self.assertEqual(result.duplicates, 1)

    def test_select_feeds_rejects_unknown_source(self):
        with self.assertRaisesRegex(ValueError, "Unknown source"):
            select_feeds(["Not a source"])


if __name__ == "__main__":
    unittest.main()
