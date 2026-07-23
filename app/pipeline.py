import argparse
import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.analyzer.ai_analyzer import AnalysisRunSummary, run_analysis
from app.collector.rss_collector import (
    CollectionSummary,
    positive_int,
    run_collection,
    select_feeds,
)

DEFAULT_ANALYSIS_LIMIT = 100
DEFAULT_LOCK_PATH = Path("/tmp/nestor-insight-pipeline.lock")


class PipelineAlreadyRunning(RuntimeError):
    pass


@dataclass
class PipelineRunSummary:
    collection: CollectionSummary
    analysis: AnalysisRunSummary
    dry_run: bool

    @property
    def failed(self) -> bool:
        collection_write_failures = sum(
            result.write_failures for result in self.collection.results
        )
        return bool(
            self.collection.failed_sources
            or collection_write_failures
            or self.analysis.failed
            or self.analysis.skip_write_failures
        )


@contextmanager
def pipeline_lock(lock_path: Path = DEFAULT_LOCK_PATH):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PipelineAlreadyRunning(
                f"pipeline lock is already held: {lock_path}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def run_pipeline(
    analysis_limit: int = DEFAULT_ANALYSIS_LIMIT,
    feeds: list[dict] | None = None,
    max_per_feed: int | None = None,
    dry_run: bool = False,
    lock_path: Path = DEFAULT_LOCK_PATH,
) -> PipelineRunSummary:
    with pipeline_lock(lock_path):
        collection = run_collection(
            feeds=feeds,
            max_per_feed=max_per_feed,
            dry_run=dry_run,
        )
        analysis = run_analysis(
            limit=analysis_limit,
            retry_failed=True,
            dry_run=dry_run,
        )

    summary = PipelineRunSummary(
        collection=collection,
        analysis=analysis,
        dry_run=dry_run,
    )
    print(
        "Pipeline completed: "
        f"inserted={collection.total_inserted}, "
        f"analyzed={analysis.success}, "
        f"skipped={analysis.skipped}, "
        f"failed={summary.failed}, "
        f"dry_run={dry_run}"
    )
    return summary


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run collection and analysis as one controlled pipeline."
    )
    parser.add_argument(
        "--analysis-limit",
        type=positive_int,
        default=DEFAULT_ANALYSIS_LIMIT,
        help=(
            "maximum queued articles sent to Claude "
            f"(default: {DEFAULT_ANALYSIS_LIMIT})"
        ),
    )
    parser.add_argument(
        "--source",
        action="append",
        help="collect one exact RSS source; repeat to select multiple sources",
    )
    parser.add_argument(
        "--max-per-feed",
        type=positive_int,
        help="limit entries inspected from each selected feed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview collection and analysis without writes or Claude calls",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        chosen_feeds = select_feeds(args.source)
        result = run_pipeline(
            analysis_limit=args.analysis_limit,
            feeds=chosen_feeds,
            max_per_feed=args.max_per_feed,
            dry_run=args.dry_run,
        )
    except (PipelineAlreadyRunning, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    if result.failed:
        raise SystemExit(1)
