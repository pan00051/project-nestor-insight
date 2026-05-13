import feedparser
import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

RSS_FEEDS = [
    {"name": "Reuters Technology", "url": "https://feeds.reuters.com/reuters/technologyNews"},
    {"name": "BBC News", "url": "http://feeds.bbci.co.uk/news/rss.xml"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage"},
]


def parse_published_at(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated"):
        s = getattr(entry, attr, None)
        if s:
            try:
                return parsedate_to_datetime(s)
            except Exception:
                pass
    return None


def collect_feed(feed_name: str, feed_url: str) -> int:
    print(f"  抓取: {feed_name}")
    parsed = feedparser.parse(feed_url)

    if parsed.bozo and not parsed.entries:
        print(f"    [错误] 解析失败: {parsed.bozo_exception}")
        return 0

    new_count = 0
    for entry in parsed.entries:
        url = entry.get("link", "").strip()
        title = entry.get("title", "").strip()
        if not url or not title:
            continue

        summary = entry.get("summary", "") or entry.get("description", "")

        article = {
            "title": title,
            "url": url,
            "source": feed_name,
            "feed_url": feed_url,
            "published_at": (parse_published_at(entry) or datetime.now(timezone.utc)).isoformat(),
            "summary": summary[:2000] if summary else None,
        }

        try:
            result = (
                supabase.table("articles")
                .upsert(article, on_conflict="url", ignore_duplicates=True)
                .execute()
            )
            if result.data:
                new_count += 1
        except Exception as e:
            print(f"    [跳过] {url[:60]}... 原因: {e}")

    return new_count


def run_collection():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始抓取")
    total = 0
    for feed in RSS_FEEDS:
        count = collect_feed(feed["name"], feed["url"])
        print(f"    新增 {count} 条")
        total += count
    print(f"本轮共新增 {total} 条文章\n")
    return total


if __name__ == "__main__":
    run_collection()
