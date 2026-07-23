import unittest
from datetime import datetime, timedelta, timezone

from app.collector.rss_collector import article_content_hash


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


if __name__ == "__main__":
    unittest.main()
