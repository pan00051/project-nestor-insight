import json
import os
import time
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

PROMPT_TEMPLATE = """\
Analyze the following AI industry news article as a business development and market intelligence signal.
Return ONLY a JSON object with no other text.

Title: {title}
Summary: {summary}

Return this exact JSON structure:
{{
  "event_type": "<one of: technology, politics, business, science, other>",
  "sentiment": "<one of: positive, negative, neutral>",
  "importance": <integer 1-10>,
  "entities": ["<name>", "..."],
  "one_line_summary": "<max 20 words in English>",
  "signal_type": "<one of: funding_event, product_launch, leadership_change, market_expansion, partnership, hiring_growth, regulatory_risk, competitor_move, enterprise_adoption, security_incident, pricing_change, research_breakthrough, other>",
  "why_it_matters": "<one sentence explaining why this signal matters>",
  "business_implication": "<one sentence explaining the likely market or BD implication>",
  "suggested_action": "<one short action a BD, product, or market analysis team should take>",
  "target_persona": "<one of: founder, product_leader, sales_leader, marketing_leader, investor, analyst, engineer, policy_leader, other>",
  "urgency": <integer 1-10>
}}"""


def analyze_article(title: str, summary: str) -> dict:
    prompt = PROMPT_TEMPLATE.format(
        title=title,
        summary=(summary or "")[:1000],
    )
    message = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def run_analysis():
    result = (
        supabase.table("articles")
        .select("id, title, summary")
        .or_("analyzed_at.is.null,signal_type.is.null")
        .order("id")
        .execute()
    )
    articles = result.data
    total = len(articles)

    if total == 0:
        print("没有待分析或待升级的文章。")
        return

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始分析/升级，共 {total} 篇\n")

    success, failed = 0, 0
    for i, article in enumerate(articles, 1):
        article_id = article["id"]
        title = article["title"] or ""
        summary = article["summary"] or ""

        try:
            analysis = analyze_article(title, summary)
            supabase.table("articles").update(
                {
                    "event_type": analysis.get("event_type"),
                    "sentiment": analysis.get("sentiment"),
                    "importance": analysis.get("importance"),
                    "entities": analysis.get("entities", []),
                    "one_line_summary": analysis.get("one_line_summary"),
                    "signal_type": analysis.get("signal_type"),
                    "why_it_matters": analysis.get("why_it_matters"),
                    "business_implication": analysis.get("business_implication"),
                    "suggested_action": analysis.get("suggested_action"),
                    "target_persona": analysis.get("target_persona"),
                    "urgency": analysis.get("urgency"),
                    "analyzed_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", article_id).execute()

            success += 1
            print(f"  [{i}/{total}] ✓ id={article_id} | {title[:50]}")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{total}] ✗ id={article_id} | 原因: {e}")

        time.sleep(0.5)

    print(f"\n完成：成功 {success} 篇，失败 {failed} 篇\n")


if __name__ == "__main__":
    run_analysis()
