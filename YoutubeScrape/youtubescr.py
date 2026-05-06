import json
import re
import os
import time
from collections import Counter
import yt_dlp
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
MAX_COMMENTS_PER_VIDEO = 200         # 1 page = 100 comments, 2 pages = 200
DELAY     = 1                        # seconds between requests

# Output path — save alongside the Reddit scraper's leads.json
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_FILE  = os.path.join(PROJECT_ROOT, "output", "yt_leads.json")

# ── Target Channels & Search Queries (from OSINT doc) ────────────────────────
TARGET_CHANNELS = [
    "DrAnujPachhel",               # Replace with channel handle without @ if needed, or with @
]

SEARCH_QUERIES = [
    "First 24 hour internship duty vlog India",
    "NEET PG hell week mbbs",
    "MBBS intern vlog India hospital",
    "Indian resident doctor burnout",
    "MD residency toxic seniors India",
]

# ── Pain Point Keywords ───────────────────────────────────────────────────────
PAIN_KEYWORDS = {
    "documentation":  ["discharge summary", "lama", "dama", "case summary",
                       "logbook", "paperwork", "notes"],
    "workload":       ["24 hour duty", "24-hour duty", "24hr duty",
                       "36 hour", "36-hour", "night shift", "scut work",
                       "iv line", "foley", "no sleep", "patient load",
                       "exhausted", "overwork"],
    "toxic_culture":  ["ragging", "toxic senior", "verbal abuse", "humiliation",
                       "burnout", "quit medicine", "depressed", "regret mbbs"],
    "research":       ["spss", "thesis", "dissertation", "p-value", "p value",
                       "guide not helping", "reproducible"],
    "exam_stress":    ["neet pg", "neet-pg", "ini cet", "inicet", "ini-cet",
                       "fmge", "prof exam", "rank anxiety",
                       "failed", "attempt"],
    "financial":      ["stipend delay", "stipend delayed",
                       "service bond", "bond period", "bond amount",
                       "unpaid", "not paid"],
    "tech_friction":  ["app crash", "ehr", "emr", "slow ui",
                       "no customer support", "voice to text",
                       "voice-to-text"],
}

# Pre-compile regexes for each keyword (word-boundary matching)
_COMPILED_KEYWORDS: dict[str, list[tuple[str, re.Pattern]]] = {}
for _cat, _kws in PAIN_KEYWORDS.items():
    _COMPILED_KEYWORDS[_cat] = [
        (kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE))
        for kw in _kws
    ]

# ── Pain Point Detection ──────────────────────────────────────────────────────
def detect_pain_points(text: str) -> list[dict]:
    """Return list of {category, keyword} dicts using word-boundary regex."""
    matches = []
    for cat, compiled_kws in _COMPILED_KEYWORDS.items():
        for kw, pattern in compiled_kws:
            if pattern.search(text):
                matches.append({"category": cat, "keyword": kw})
    return matches

def _snippet(text: str, keyword: str, window: int = 200) -> str:
    """
    Extract a snippet of `window` chars around the first occurrence
    of `keyword` in `text`.  Falls back to the first `window` chars.
    """
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return text[:window].strip()
    start = max(0, idx - window // 2)
    end = min(len(text), idx + len(keyword) + window // 2)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet

def _utc_iso(ts) -> str:
    """Convert a Unix timestamp to ISO-8601 string."""
    if not ts:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

# ── YT-DLP Helpers ────────────────────────────────────────────────────────────
def search_videos(query: str, max_results: int = 5) -> list[dict]:
    """Search YouTube for videos matching a query using yt-dlp."""
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            return [
                {
                    "video_id": item.get("id"),
                    "title": item.get("title"),
                    "channel": item.get("channel") or item.get("uploader"),
                    "url": item.get("url"),
                }
                for item in info.get("entries", []) if item.get("id")
            ]
        except Exception as e:
            print(f"    ✗ Search error: {e}")
            return []

def get_channel_videos(channel_id: str, max_results: int = 5) -> list[dict]:
    """Get recent uploads from a channel using yt-dlp."""
    # Handle channel IDs or handles
    if not channel_id.startswith('@') and not channel_id.startswith('UC'):
        channel_id = f"@{channel_id}"
        
    url = f"https://www.youtube.com/{channel_id}/videos"
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'playlistend': max_results,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return [
                {
                    "video_id": item.get("id"),
                    "title": item.get("title"),
                    "channel": item.get("channel") or item.get("uploader"),
                    "url": item.get("url"),
                }
                for item in info.get("entries", []) if item.get("id")
            ]
        except Exception as e:
            print(f"    ✗ Channel fetch error: {e}")
            return []

def get_comments(url: str, max_comments: int = MAX_COMMENTS_PER_VIDEO) -> list[dict]:
    """Fetch top-level comments and replies for a video using yt-dlp."""
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'getcomments': True,
        'extractor_args': {'youtube': {'max_comments': [str(max_comments), 'all', 'all']}}
    }
    comments_list = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            comments = info.get("comments", [])
            for c in comments:
                comments_list.append({
                    "comment_id": c.get("id"),
                    "author": c.get("author", ""),
                    "text": c.get("text", ""),
                    "like_count": c.get("like_count", 0),
                    "published_at": _utc_iso(c.get("timestamp")),
                })
        except Exception as e:
            print(f"    ✗ Comment fetch error: {e}")
    return comments_list

# ── Main Scraper ──────────────────────────────────────────────────────────────
def scrape() -> list[dict]:
    leads     = []
    seen_ids  = set()
    all_videos = []

    # 1. Videos from search queries
    for query in SEARCH_QUERIES:
        print(f"\n🔍 Searching: \"{query}\"")
        videos = search_videos(query, max_results=5)
        all_videos.extend(videos)
        print(f"   Found {len(videos)} videos.")
        time.sleep(DELAY)

    # 2. Videos from specific channels
    for channel_id in TARGET_CHANNELS:
        print(f"\n📺 Fetching channel: {channel_id}")
        videos = get_channel_videos(channel_id, max_results=5)
        all_videos.extend(videos)
        print(f"   Found {len(videos)} videos.")
        time.sleep(DELAY)

    # Deduplicate videos
    seen_vids = set()
    unique_videos = []
    for v in all_videos:
        if v["video_id"] not in seen_vids:
            seen_vids.add(v["video_id"])
            unique_videos.append(v)

    print(f"\n🎬 Total unique videos to scan: {len(unique_videos)}")

    # 3. Scrape comments per video
    for video in unique_videos:
        vid_url = video.get("url") or f"https://www.youtube.com/watch?v={video['video_id']}"
        title = video.get("title", "")
        channel = video.get("channel") or "Unknown Channel"
        print(f"\n💬 Scraping comments: \"{title[:60]}\"")
        comments = get_comments(vid_url)
        print(f"   Fetched {len(comments)} comments", end=" → ")

        pain_count = 0
        for comment in comments:
            cid = comment.get("comment_id")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)

            text = comment.get("text", "")
            matches = detect_pain_points(text)
            if not matches:
                continue

            # One lead per unique category
            seen_cats = set()
            for m in matches:
                cat = m["category"]
                if cat in seen_cats:
                    continue
                seen_cats.add(cat)

                pain_text = _snippet(text, m["keyword"])
                leads.append({
                    "user_id":    comment.get("author", "unknown_user"),
                    "pain_point": f"[{cat}] {pain_text}",
                    "email":      None,
                    "phone":      None,
                    "source":     f"youtube/{channel} — {vid_url}",
                    "created_at": comment.get("published_at"),
                    "processed":  False,
                })
                pain_count += 1

        print(f"{pain_count} pain-point leads found.")
        time.sleep(DELAY)

    return leads


# ── Save & Report ─────────────────────────────────────────────────────────────
def save(leads: list[dict]):
    os.makedirs(os.path.dirname(OUTPUT_FILE) or ".", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(leads, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Saved {len(leads)} leads.")
    print(f"   → {OUTPUT_FILE}")

    # Category breakdown from the [Category] prefix in pain_point
    import re as _re
    cats = [_re.match(r"\[(.+?)\]", l["pain_point"]).group(1)
            for l in leads if _re.match(r"\[(.+?)\]", l["pain_point"])]
    print("\n📊 Category breakdown:")
    for cat, count in Counter(cats).most_common():
        print(f"   {cat:<20} {count}")


if __name__ == "__main__":
    leads = scrape()
    save(leads)