import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

import httpx

from app.collector.historical_backfill import (
    fetch_gdelt_window,
    gdelt_article_to_record,
    iter_date_windows,
    process_window,
)


class FakeHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, _url, **_kwargs):
        response = self.responses[self.calls]
        self.calls += 1
        return response


class FakeBackfillQuery:
    def __init__(self, existing_hashes=None, existing_urls=None):
        self.existing_values = {
            "content_hash": set(existing_hashes or []),
            "url": set(existing_urls or []),
        }
        self.field = None
        self.values = []
        self.mode = "select"
        self.pending_articles = []
        self.upsert_calls = 0

    def select(self, field):
        self.mode = "select"
        self.field = field
        return self

    def in_(self, field, values):
        self.field = field
        self.values = list(values)
        return self

    def upsert(self, articles, **_kwargs):
        self.mode = "upsert"
        self.upsert_calls += 1
        self.pending_articles = (
            articles if isinstance(articles, list) else [articles]
        )
        return self

    def execute(self):
        if self.mode == "select":
            rows = [
                {self.field: value}
                for value in self.values
                if value in self.existing_values[self.field]
            ]
            return SimpleNamespace(data=rows)
        return SimpleNamespace(data=self.pending_articles)


class FakeSupabase:
    def __init__(self, existing_hashes=None, existing_urls=None):
        self.query = FakeBackfillQuery(existing_hashes, existing_urls)

    def table(self, _name):
        return self.query


def response(status_code, payload=None, headers=None):
    request = httpx.Request("GET", "https://api.gdeltproject.org/api/v2/doc/doc")
    return httpx.Response(
        status_code,
        request=request,
        json=payload,
        headers=headers,
    )


class HistoricalBackfillTests(unittest.TestCase):
    def test_date_windows_run_newest_to_oldest_without_overlap(self):
        windows = list(
            iter_date_windows(
                date(2026, 7, 1),
                date(2026, 7, 10),
                window_days=4,
            )
        )

        self.assertEqual(
            [(start.date(), end.date()) for start, end in windows],
            [
                (date(2026, 7, 7), date(2026, 7, 10)),
                (date(2026, 7, 3), date(2026, 7, 6)),
                (date(2026, 7, 1), date(2026, 7, 2)),
            ],
        )

    def test_gdelt_article_conversion_normalizes_url_and_timestamp(self):
        record = gdelt_article_to_record(
            {
                "title": "  OpenAI   launches enterprise agents ",
                "url": "HTTPS://Example.COM/news/?utm_source=gdelt&id=7#top",
                "domain": "Example.COM",
                "seendate": "20260720T123456Z",
            }
        )

        self.assertEqual(record["title"], "OpenAI launches enterprise agents")
        self.assertEqual(record["url"], "https://example.com/news?id=7")
        self.assertEqual(record["source"], "example.com")
        self.assertEqual(
            record["published_at"],
            "2026-07-20T12:34:56+00:00",
        )
        self.assertIsNone(record["summary"])
        self.assertEqual(len(record["content_hash"]), 64)

    def test_fetch_honors_retry_after_on_rate_limit(self):
        sleeps = []
        client = FakeHttpClient(
            [
                response(429, {"error": "rate limited"}, {"Retry-After": "12"}),
                response(200, {"articles": [{"title": "OpenAI update"}]}),
            ]
        )

        articles, attempts = fetch_gdelt_window(
            http_client=client,
            query="OpenAI",
            start=datetime(2026, 7, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 2, tzinfo=timezone.utc),
            max_records=10,
            sleep_fn=sleeps.append,
        )

        self.assertEqual(attempts, 2)
        self.assertEqual(len(articles), 1)
        self.assertEqual(sleeps, [12.0])

    def test_process_window_filters_and_deduplicates_without_writing(self):
        existing_url = "https://example.com/existing"
        database = FakeSupabase(existing_urls={existing_url})
        articles = [
            {
                "title": "OpenAI launches an enterprise AI platform",
                "url": "https://example.com/new?utm_source=gdelt",
                "domain": "example.com",
                "seendate": "20260720T100000Z",
            },
            {
                "title": "OpenAI expands its enterprise product",
                "url": existing_url,
                "domain": "example.com",
                "seendate": "20260720T110000Z",
            },
            {
                "title": "Local football club announces new coach",
                "url": "https://example.com/sports",
                "domain": "example.com",
                "seendate": "20260720T120000Z",
            },
            {
                "title": "",
                "url": "https://example.com/invalid",
                "domain": "example.com",
                "seendate": "20260720T130000Z",
            },
        ]
        client = FakeHttpClient([response(200, {"articles": articles})])

        result = process_window(
            supabase_client=database,
            http_client=client,
            query="OpenAI",
            start=datetime(2026, 7, 20, tzinfo=timezone.utc),
            end=datetime(2026, 7, 20, 23, 59, tzinfo=timezone.utc),
            max_records=10,
            min_relevance=3,
            remaining_target=10,
            write=False,
            run_seen_hashes=set(),
            run_seen_urls=set(),
            sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.fetched, 4)
        self.assertEqual(result.relevant, 2)
        self.assertEqual(result.new_candidates, 1)
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(result.invalid, 1)
        self.assertEqual(result.inserted, 0)
        self.assertEqual(database.query.upsert_calls, 0)


if __name__ == "__main__":
    unittest.main()
