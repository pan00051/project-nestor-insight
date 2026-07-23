import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.analyzer.ai_analyzer import (
    AnalysisResult,
    analyze_article,
    article_quality_issue,
    fetch_analysis_batch,
    normalize_analysis_payload,
    persist_skip_decisions,
    relevance_score,
    run_analysis,
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

    def in_(self, *_args):
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


class FakeStatusQuery:
    def __init__(self):
        self.saved = []
        self.payload = None
        self.article_id = None

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, _field, article_id):
        self.article_id = article_id
        return self

    def execute(self):
        self.saved.append((self.article_id, self.payload))
        return SimpleNamespace(data=[{"id": self.article_id}])


class FakeStatusSupabase:
    def __init__(self):
        self.query = FakeStatusQuery()

    def table(self, _name):
        return self.query


class FakePipelineQuery:
    def __init__(self, articles):
        self.articles = articles
        self.start = 0
        self.end = 0
        self.mode = "select"
        self.pending_payload = None
        self.pending_id = None
        self.saved = []

    def select(self, *_args):
        self.mode = "select"
        return self

    def in_(self, *_args):
        return self

    def order(self, *_args):
        return self

    def range(self, start, end):
        self.start = start
        self.end = end
        return self

    def update(self, payload):
        self.mode = "update"
        self.pending_payload = payload
        return self

    def eq(self, _field, article_id):
        self.pending_id = article_id
        return self

    def execute(self):
        if self.mode == "update":
            self.saved.append((self.pending_id, self.pending_payload))
            return SimpleNamespace(data=[{"id": self.pending_id}])
        return SimpleNamespace(data=self.articles[self.start : self.end + 1])


class FakePipelineSupabase:
    def __init__(self, articles):
        self.query = FakePipelineQuery(articles)

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

    def test_signal_type_misplaced_in_event_type_is_mapped(self):
        payload = normalize_analysis_payload(
            {
                **VALID_ANALYSIS,
                "event_type": "security_incident",
            }
        )
        result = AnalysisResult.model_validate(payload)
        self.assertEqual(result.event_type, "technology")

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
            selected, stats, status_updates = fetch_analysis_batch(
                FakeSupabase(articles),
                limit=10,
                min_relevance=2,
            )

        self.assertEqual([article["id"] for article in selected], [2, 5])
        self.assertEqual(stats["scanned"], 5)
        self.assertEqual(stats["low_relevance"], 1)
        self.assertEqual(stats["duplicate_title"], 1)
        self.assertEqual(stats["invalid"], 1)
        self.assertEqual(
            [status_update["id"] for status_update in status_updates],
            [1, 3, 4],
        )
        self.assertTrue(
            all(
                status_update["analysis_status"] == "skipped"
                for status_update in status_updates
            )
        )

    def test_persist_skip_decisions_writes_pipeline_state(self):
        fake_supabase = FakeStatusSupabase()
        saved, failed = persist_skip_decisions(
            fake_supabase,
            [
                {
                    "id": 12,
                    "analysis_status": "skipped",
                    "relevance_score": 0,
                    "skip_reason": "below threshold",
                }
            ],
        )

        self.assertEqual((saved, failed), (1, 0))
        article_id, payload = fake_supabase.query.saved[0]
        self.assertEqual(article_id, 12)
        self.assertEqual(payload["analysis_status"], "skipped")
        self.assertIn("analysis_attempted_at", payload)

    def test_run_analysis_persists_analyzed_state(self):
        fake_supabase = FakePipelineSupabase(
            [
                {
                    "id": 21,
                    "title": "OpenAI launches a new enterprise model",
                    "summary": "The AI release targets regulated teams.",
                    "analysis_attempts": 0,
                }
            ]
        )
        fake_claude = FakeClaude(__import__("json").dumps(VALID_ANALYSIS))

        with (
            patch(
                "app.analyzer.ai_analyzer.get_supabase_client",
                return_value=fake_supabase,
            ),
            patch(
                "app.analyzer.ai_analyzer.get_claude_client",
                return_value=fake_claude,
            ),
            patch("app.analyzer.ai_analyzer.time.sleep"),
        ):
            run_analysis(limit=1)

        article_id, payload = fake_supabase.query.saved[-1]
        self.assertEqual(article_id, 21)
        self.assertEqual(payload["analysis_status"], "analyzed")
        self.assertEqual(payload["analysis_attempts"], 1)
        self.assertIsNone(payload["analysis_error"])

    def test_run_analysis_persists_failed_state(self):
        fake_supabase = FakePipelineSupabase(
            [
                {
                    "id": 22,
                    "title": "Anthropic announces a new Claude model",
                    "summary": "The AI model targets enterprise workflows.",
                    "analysis_attempts": 2,
                }
            ]
        )
        fake_claude = FakeClaude("not-json")

        with (
            patch(
                "app.analyzer.ai_analyzer.get_supabase_client",
                return_value=fake_supabase,
            ),
            patch(
                "app.analyzer.ai_analyzer.get_claude_client",
                return_value=fake_claude,
            ),
            patch("app.analyzer.ai_analyzer.time.sleep"),
        ):
            run_analysis(limit=1)

        article_id, payload = fake_supabase.query.saved[-1]
        self.assertEqual(article_id, 22)
        self.assertEqual(payload["analysis_status"], "failed")
        self.assertEqual(payload["analysis_attempts"], 3)
        self.assertIn("JSON object", payload["analysis_error"])
        self.assertIsNone(payload["skip_reason"])


if __name__ == "__main__":
    unittest.main()
