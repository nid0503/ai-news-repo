#!/usr/bin/env python3
"""
AI News Substack Aggregator
Fetches RSS feeds from top AI newsletters, deduplicates, and saves as daily Markdown files.
"""

import json
import os
import sys
import hashlib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Ensure UTF-8 output on Windows (avoids emoji/unicode errors)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import feedparser
    import yaml
    import requests
except ImportError:
    print("Missing dependencies. Run: pip install feedparser pyyaml requests")
    sys.exit(1)

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
FEEDS_FILE  = BASE_DIR / "feeds.yaml"
SEEN_FILE   = BASE_DIR / "seen_urls.json"
OUTPUT_DIR  = BASE_DIR / "output"
TODAY       = datetime.now().strftime("%Y-%m-%d")
OUTPUT_FILE = OUTPUT_DIR / f"{TODAY}.md"


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_seen_urls() -> set:
    """Load the set of already-saved post URLs."""
    if SEEN_FILE.exists():
        try:
            # utf-8-sig handles BOM that PowerShell sometimes writes
            with open(SEEN_FILE, "r", encoding="utf-8-sig") as f:
                content = f.read().strip()
            if not content or content == "{}":
                return set()
            data = json.loads(content)
            return set(data.keys())
        except (json.JSONDecodeError, Exception):
            return set()
    return set()


def save_seen_urls(seen: set, new_entries: dict) -> None:
    """Persist seen URLs + metadata to disk."""
    existing = {}
    if SEEN_FILE.exists():
        try:
            with open(SEEN_FILE, "r", encoding="utf-8-sig") as f:
                content = f.read().strip()
            if content and content != "{}":
                existing = json.loads(content)
        except (json.JSONDecodeError, Exception):
            existing = {}
    existing.update(new_entries)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


def fetch_feed(feed: dict) -> tuple[str, list[dict], str | None]:
    """
    Fetch and parse a single RSS feed.
    Returns (feed_name, list_of_posts, error_or_None).
    """
    name = feed["name"]
    url  = feed["url"]
    try:
        # Use requests for better timeout + header control
        resp = requests.get(url, timeout=15, headers={"User-Agent": "AINewsFetcher/1.0"})
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        posts = []
        for entry in parsed.entries:
            link    = getattr(entry, "link", None) or getattr(entry, "id", None) or ""
            title   = getattr(entry, "title", "(no title)")
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            # Clean up HTML tags simply
            import re
            summary = re.sub(r"<[^>]+>", "", summary).strip()
            summary = summary[:400] + "…" if len(summary) > 400 else summary

            # Published date
            published = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d")
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6]).strftime("%Y-%m-%d")

            posts.append({
                "url":       link,
                "title":     title,
                "summary":   summary,
                "published": published,
                "source":    name,
            })
        return name, posts, None
    except Exception as e:
        return name, [], str(e)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Load config
    if not FEEDS_FILE.exists():
        print(f"ERROR: feeds.yaml not found at {FEEDS_FILE}")
        sys.exit(1)

    with open(FEEDS_FILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    feeds = config.get("feeds", [])

    if not feeds:
        print("No feeds configured in feeds.yaml")
        sys.exit(0)

    OUTPUT_DIR.mkdir(exist_ok=True)
    seen_urls = load_seen_urls()

    print(f"\n🔍 Fetching {len(feeds)} feeds in parallel...\n")

    # Fetch all feeds concurrently
    all_results = {}
    errors = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_feed, feed): feed for feed in feeds}
        for future in as_completed(futures):
            name, posts, error = future.result()
            all_results[name] = posts
            if error:
                errors.append((name, error))
                print(f"  ⚠️  {name}: {error}")
            else:
                print(f"  ✅  {name}: {len(posts)} posts found")

    # Filter to new posts only
    new_posts = []
    new_entries_meta = {}

    for name, posts in all_results.items():
        for post in posts:
            url = post["url"]
            if not url or url in seen_urls:
                continue
            new_posts.append(post)
            new_entries_meta[url] = {
                "saved_on": TODAY,
                "source":   name,
                "title":    post["title"],
            }

    # Summary
    print(f"\n{'─'*50}")
    print(f"📰 New posts found:   {len(new_posts)}")
    print(f"📁 Sources checked:   {len(feeds)}")
    if errors:
        print(f"❌ Failed feeds:      {len(errors)} ({', '.join(e[0] for e in errors)})")
    print(f"{'─'*50}\n")

    if not new_posts:
        print("✨ No new posts since last run. Nothing to save.")
        return

    # Sort by published date descending, then by source
    new_posts.sort(key=lambda p: (p["published"] or "0000-00-00"), reverse=True)

    # Write Markdown file
    mode = "a" if OUTPUT_FILE.exists() else "w"
    with open(OUTPUT_FILE, mode, encoding="utf-8") as f:
        if mode == "w":
            f.write(f"# AI News — {TODAY}\n\n")
            f.write(f"*Generated by AI News Aggregator · {len(new_posts)} new posts from {len(all_results)} sources*\n\n")
            f.write("---\n\n")
        else:
            f.write(f"\n---\n\n*Appended at {datetime.now().strftime('%H:%M')} · {len(new_posts)} additional posts*\n\n")

        # Group by source
        from collections import defaultdict
        by_source = defaultdict(list)
        for post in new_posts:
            by_source[post["source"]].append(post)

        for source, posts in sorted(by_source.items()):
            f.write(f"## {source}\n\n")
            for post in posts:
                date_str = f" ·  {post['published']}" if post["published"] else ""
                f.write(f"### [{post['title']}]({post['url']}){date_str}\n\n")
                if post["summary"]:
                    f.write(f"{post['summary']}\n\n")

    # Persist seen URLs
    save_seen_urls(seen_urls, new_entries_meta)

    print(f"✅ Saved to: {OUTPUT_FILE}")
    print(f"   ({len(new_posts)} posts across {len(by_source)} sources)\n")


if __name__ == "__main__":
    main()
