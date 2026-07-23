import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.analyzer.ai_analyzer import (
    AnalysisResult,
    analyze_article,
    article_quality_issue,
    fetch_analysis_batch,
    relevance_score,
    strip_html,
)


VALID_ANALYSIS = {
    "event_type": "technology",
    "sentiment": "positive",
    "importance": 8,
    "entities": ["OpenAI", "OpenAI", " Microsoft "],
    "one_line_summary": (
        "OpenAI launches a new enterprise AI model for regulated industries "
        "with improved controls and deployment options today"
    ),
    "signal_type": "product_launch",
    "why_it_matters": "The launch changes enterprise AI buying decisions.",
    "business_implication": "Competitors may need to adjust their positioning.",
    "suggested_action": "Review the launch against the current product roadmap.",
    "target_persona": "product_leader",
    "urgency": 8,
}


class FakeMessages:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(text=self.text)])


class FakeClaude:
    def __init__(self, text):
        self.messages = FakeMessages(text)


class FakeArticleQuery:
    def __init__(self, articles):
        self.articles = articles
        self.start = 0
        self.end = 0

    def select(self, *_args):
        return self

    def or_(self, *_args):
        return self

    def order(self, *_args):
        return self

    def range(self, start, end):
        self.start = start
        self.end = end
        return self

    def execute(self):
        return SimpleNamespace(data=self.articles[self.start : self.end + 1])


class FakeSupabase:
    def __init__(self, articles):
        self.query = FakeArticleQuery(articles)

    def table(self, _name):
        return self.query


class AnalyzerQualityTests(unittest.TestCase):
    def test_strip_html_keeps_readable_text(self):
        value = "<p>OpenAI &amp; Microsoft <strong>expand</strong>.</p>"
        self.assertEqual(strip_html(value), "OpenAI & Microsoft expand .")

    def test_relevance_filter_accepts_ai_news(self):
        score, matches = relevance_score(
            "OpenAI launches a new enterprise model",
            "The artificial intelligence product targets regulated teams.",
        )
        self.assertGreaterEqual(score, 2)
        self.assertIn("openai", matches)

    def test_relevance_filter_rejects_unrelated_news(self):
        score, matches = relevance_score(
            "Ebola outbreak spreads rapidly",
            "Health authorities announced new containment measures.",
        )
        self.assertLess(score, 2)
        self.assertEqual(matches, [])

    def test_article_quality_rejects_missing_title(self):
        self.assertEqual(article_quality_issue({"title": ""}), "missing title")

    def test_analysis_result_normalizes_entities_and_summary(self):
        result = AnalysisResult.model_validate(VALID_ANALYSIS)
        self.assertEqual(result.entities, ["OpenAI", "Microsoft"])
        self.assertLessEqual(len(result.one_line_summary.split()), 20)

    def test_analysis_result_rejects_invalid_scores(self):
        invalid = {**VALID_ANALYSIS, "importance": 11}
        with self.assertRaises(ValidationError):
            AnalysisResult.model_validate(invalid)

    def test_analyze_article_parses_fenced_json(self):
        fake_claude = FakeClaude(f"```json\n{__import__('json').dumps(VALID_ANALYSIS)}\n```")
        result = analyze_article(
            "OpenAI launches a new model",
            "<p>Enterprise release.</p>",
            claude_client=fake_claude,
            max_attempts=1,
        )
        self.assertEqual(result["signal_type"], "product_launch")
        self.assertEqual(fake_claude.messages.calls, 1)

    def test_batch_selection_paginates_and_filters_before_limit(self):
        articles = [
            {
                "id": 1,
                "title": "Ebola outbreak spreads rapidly",
                "summary": "Health authorities announced containment measures.",
            },
            {
                "id": 2,
                "title": "OpenAI launches a new enterprise model",
                "summary": "The AI product adds controls for regulated teams.",
            },
            {
                "id": 3,
                "title": "OpenAI launches a new enterprise model",
                "summary": "A duplicate item from another feed.",
            },
            {"id": 4, "title": "", "summary": "Missing title."},
            {
                "id": 5,
                "title": "Anthropic expands Claude for enterprise users",
                "summary": "New AI administration and security features.",
            },
        ]

        with patch("app.analyzer.ai_analyzer.FETCH_PAGE_SIZE", 2):
            selected, stats = fetch_analysis_batch(
                FakeSupabase(articles),
                limit=10,
                min_relevance=2,
            )

        self.assertEqual([article["id"] for article in selected], [2, 5])
        self.assertEqual(stats["scanned"], 5)
        self.assertEqual(stats["low_relevance"], 1)
        self.assertEqual(stats["duplicate_title"], 1)
        self.assertEqual(stats["invalid"], 1)


if __name__ == "__main__":
    unittest.main()
