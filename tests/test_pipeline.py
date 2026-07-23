import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.analyzer.ai_analyzer import AnalysisRunSummary
from app.pipeline import (
    PipelineAlreadyRunning,
    pipeline_lock,
    run_pipeline,
)


def collection_summary(failed_sources=0, write_failures=0):
    return SimpleNamespace(
        failed_sources=failed_sources,
        total_inserted=3,
        results=[SimpleNamespace(write_failures=write_failures)],
    )


class PipelineTests(unittest.TestCase):
    def test_pipeline_runs_collection_before_analysis(self):
        calls = []
        analysis = AnalysisRunSummary(3, 0, 3, 0, 0, False)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "app.pipeline.run_collection",
                side_effect=lambda **_kwargs: (
                    calls.append("collect") or collection_summary()
                ),
            ),
            patch(
                "app.pipeline.run_analysis",
                side_effect=lambda **_kwargs: (
                    calls.append("analyze") or analysis
                ),
            ),
        ):
            result = run_pipeline(
                analysis_limit=10,
                lock_path=Path(directory) / "pipeline.lock",
            )

        self.assertEqual(calls, ["collect", "analyze"])
        self.assertFalse(result.failed)

    def test_pipeline_reports_component_failures(self):
        analysis = AnalysisRunSummary(1, 0, 0, 1, 0, False)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "app.pipeline.run_collection",
                return_value=collection_summary(),
            ),
            patch("app.pipeline.run_analysis", return_value=analysis),
        ):
            result = run_pipeline(
                lock_path=Path(directory) / "pipeline.lock",
            )

        self.assertTrue(result.failed)

    def test_pipeline_lock_rejects_overlapping_run(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "pipeline.lock"
            with pipeline_lock(lock_path):
                with self.assertRaises(PipelineAlreadyRunning):
                    with pipeline_lock(lock_path):
                        pass


if __name__ == "__main__":
    unittest.main()
