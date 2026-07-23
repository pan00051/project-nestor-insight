import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from app.collector.wordpress_backfill import (
    fetch_archive_page,
    process_archive_page,
    wordpress_post_to_record,
)


class FakeHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, _url, **_kwargs):
        response = self.responses[self.calls]
        self.calls += 1
        return response


class FakeQuery:
    def __init__(self, existing_urls=None):
        self.existing_urls = set(existing_urls or [])
        self.field = None
        self.values = []
        self.mode = "select"
        self.upsert_calls = 0

    def select(self, field):
        self.mode = "select"
        self.field = field
        return self

    def in_(self, field, values):
        self.field = field
        self.values = list(values)
        return self

    def upsert(self, _articles, **_kwargs):
        self.mode = "upsert"
        self.upsert_calls += 1
        return self

    def execute(self):
        if self.mode == "select":
            existing = self.existing_urls if self.field == "url" else set()
            return SimpleNamespace(
                data=[
                    {self.field: value}
                    for value in self.values
                    if value in existing
                ]
            )
        return SimpleNamespace(data=[])


class FakeSupabase:
    def __init__(self, existing_urls=None):
        self.query = FakeQuery(existing_urls)

    def table(self, _name):
        return self.query


def response(status_code, payload=None, headers=None):
    request = httpx.Request(
        "GET",
        "https://techcrunch.com/wp-json/wp/v2/posts",
    )
    return httpx.Response(
        status_code,
        request=request,
        json=payload,
        headers=headers,
    )


def post(title, link, excerpt, published="2026-07-20T10:00:00"):
    return {
        "date_gmt": published,
        "link": link,
        "title": {"rendered": title},
        "excerpt": {"rendered": excerpt},
    }


class WordPressBackfillTests(unittest.TestCase):
    def test_post_conversion_cleans_html_and_tracking(self):
        record = wordpress_post_to_record(
            post(
                "OpenAI &amp; partners launch <em>agents</em>",
                "https://techcrunch.com/story/?utm_source=archive#top",
                "<p>An <strong>AI</strong> platform for enterprises.</p>",
            )
        )

        self.assertEqual(
            record["title"],
            "OpenAI & partners launch agents",
        )
        self.assertEqual(record["url"], "https://techcrunch.com/story")
        self.assertEqual(
            record["summary"],
            "An AI platform for enterprises.",
        )
        self.assertEqual(
            record["published_at"],
            "2026-07-20T10:00:00+00:00",
        )

    def test_fetch_returns_total_page_count(self):
        client = FakeHttpClient(
            [
                response(
                    200,
                    [post("OpenAI update", "https://example.com/a", "AI")],
                    {"X-WP-TotalPages": "52"},
                )
            ]
        )

        posts, attempts, total_pages = fetch_archive_page(
            client,
            page=1,
            page_size=100,
            search_query="artificial intelligence",
        )

        self.assertEqual(len(posts), 1)
        self.assertEqual(attempts, 1)
        self.assertEqual(total_pages, 52)

    def test_process_page_filters_duplicates_and_does_not_write_in_preview(self):
        existing_url = "https://techcrunch.com/existing"
        posts = [
            post(
                "Enterprise launch",
                "https://techcrunch.com/new?utm_source=archive",
                "OpenAI introduces an artificial intelligence platform.",
            ),
            post(
                "Anthropic expands Claude",
                existing_url,
                "Claude adds enterprise AI controls.",
            ),
            post(
                "Startup opens Barcelona office",
                "https://techcrunch.com/unrelated",
                "The company plans to hire sales staff.",
            ),
        ]
        client = FakeHttpClient(
            [response(200, posts, {"X-WP-TotalPages": "1"})]
        )
        database = FakeSupabase(existing_urls={existing_url})

        result, total_pages = process_archive_page(
            supabase_client=database,
            http_client=client,
            page=1,
            page_size=100,
            search_query="artificial intelligence",
            min_relevance=2,
            remaining_target=10,
            write=False,
            run_seen_hashes=set(),
            run_seen_urls=set(),
            sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(total_pages, 1)
        self.assertEqual(result.relevant, 2)
        self.assertEqual(result.new_candidates, 1)
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(result.inserted, 0)
        self.assertEqual(database.query.upsert_calls, 0)


if __name__ == "__main__":
    unittest.main()
