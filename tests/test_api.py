import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.api import main as api


class FakeStatsQuery:
    def __init__(self, rows, ranges):
        self.rows = rows
        self.ranges = ranges
        self.start = 0
        self.end = 0

    @property
    def not_(self):
        return self

    def select(self, *_args):
        return self

    def is_(self, *_args):
        return self

    def order(self, *_args):
        return self

    def range(self, start, end):
        self.start = start
        self.end = end
        self.ranges.append((start, end))
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows[self.start : self.end + 1])


class FakeStatsSupabase:
    def __init__(self, rows):
        self.rows = rows
        self.ranges = []

    def table(self, _name):
        return FakeStatsQuery(self.rows, self.ranges)


class ApiStatsTests(unittest.TestCase):
    def test_stats_paginates_beyond_supabase_row_limit(self):
        rows = [
            {
                "id": index,
                "event_type": "business",
                "signal_type": "funding_event",
                "sentiment": "positive",
                "importance": 8,
            }
            for index in range(1328)
        ]
        fake_supabase = FakeStatsSupabase(rows)

        with patch.object(api, "supabase", fake_supabase):
            result = api.get_stats()

        self.assertEqual(result.total, 1328)
        self.assertEqual(result.avg_importance, 8.0)
        self.assertEqual(result.by_signal_type, {"funding_event": 1328})
        self.assertEqual(
            fake_supabase.ranges,
            [(0, 499), (500, 999), (1000, 1499)],
        )


if __name__ == "__main__":
    unittest.main()
