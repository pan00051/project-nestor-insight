import feedparser
import hashlib
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

RSS_FEEDS = [
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml"},
    {"name": "Wired", "url": "https://www.wired.com/feed/rss"},
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/"},
    {"name": "VentureBeat", "url": "https://venturebeat.com/feed/"},
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"name": "TechRepublic", "url": "https://www.techrepublic.com/rssfeeds/articles/"},
    {"name": "ZDNet", "url": "https://www.zdnet.com/news/rss.xml"},
    {"name": "InfoQ", "url": "https://feed.infoq.com"},
]


def get_supabase_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def normalize_title(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def article_content_hash(title: str, published_at: datetime | None) -> str:
    if published_at is None:
        published_date = ""
    else:
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        published_date = published_at.astimezone(timezone.utc).date().isoformat()

    hash_input = f"{normalize_title(title).lower()}|{published_date}"
    return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()


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


def collect_feed(feed_name: str, feed_url: str, supabase_client=None) -> int:
    client = supabase_client or get_supabase_client()
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
        published_at = parse_published_at(entry) or datetime.now(timezone.utc)
        content_hash = article_content_hash(title, published_at)

        article = {
            "title": title,
            "url": url,
            "source": feed_name,
            "feed_url": feed_url,
            "published_at": published_at.isoformat(),
            "summary": summary[:2000] if summary else None,
            "content_hash": content_hash,
        }

        try:
            duplicate = (
                client.table("articles")
                .select("id")
                .eq("content_hash", content_hash)
                .limit(1)
                .execute()
            )
            if duplicate.data:
                continue

            result = (
                client.table("articles")
                .upsert(article, on_conflict="url", ignore_duplicates=True)
                .execute()
            )
            if result.data:
                new_count += 1
        except Exception as e:
            print(f"    [跳过] {url[:60]}... 原因: {e}")

    return new_count


def run_collection(supabase_client=None):
    client = supabase_client or get_supabase_client()
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始抓取")
    total = 0
    for feed in RSS_FEEDS:
        count = collect_feed(feed["name"], feed["url"], supabase_client=client)
        print(f"    新增 {count} 条")
        total += count
    print(f"本轮共新增 {total} 条文章\n")
    return total


if __name__ == "__main__":
    run_collection()
