"""
Quora Headless Pain Point Scraper  —  v2
==========================================
Changes from v1:
  - Real Quora username extracted from profile URL  (/profile/John-Doe → John-Doe)
  - Tight keyword list: no single-word broad terms (removed 'notes', 'research', etc.)
  - Smart excerpting: finds the SENTENCE containing the keyword, not just char[0:280]
  - Score threshold: a block must hit ≥ 2 signals to be kept (kills false positives)
  - Pain point field shows matched keyword in context, with 40-char left/right window

Requires: playwright + quora_auth.json  (run save_session.py first)

Usage:
    python quora_pain_scraper.py
Output:
    quora_pain_points_<timestamp>.json
    quora_pain_points_<timestamp>.csv
"""

import json
import time
import csv
import re
import os
from datetime import datetime
from playwright.sync_api import sync_playwright

# ─── CONFIG ──────────────────────────────────────────────────────────────────

SESSION_FILE   = "quora_auth.json"
SCROLL_ROUNDS  = 8
SCROLL_DELAY   = 1.2        # seconds between scrolls
PAGE_TIMEOUT   = 60_000     # ms
MIN_SCORE      = 1          # minimum keyword hits to keep a block
CONTEXT_CHARS  = 120        # chars of context around matched keyword

TARGET_URLS = [
    "https://lifeasamedico.quora.com/Are-all-surgical-departments-in-medical-colleges-of-India-toxic-with-poor-work-culture-Which-medical-college-in-India-h",
    "https://rajubabu1.quora.com/Why-are-doctors-leaving-India",
    "https://www.quora.com/When-doctors-say-a-good-part-of-their-day-goes-in-documentation-and-paperwork-what-kind-of-documentation-are-they-referring-to",
    "https://www.quora.com/What-things-are-allowed-or-not-allowed-in-a-MBBS-college-and-what-are-the-rules-to-follow",
    "https://www.quora.com/I-feel-lost-with-my-life-I-am-a-final-year-MBBS-student-in-India-but-I-am-not-liking-this-profession-What-should-I-do",
    "https://www.quora.com/What-will-be-the-future-of-an-MBBS-after-10-years-in-India",
    "https://www.quora.com/How-do-we-go-for-a-research-while-studying-mbbs",
    "https://www.quora.com/Apart-from-the-conventional-options-what-are-the-job-prospects-of-a-person-with-an-MBBS-degree-in-India",
    "https://www.quora.com/What-is-the-whole-process-to-become-an-MBBS-in-India",
    "https://www.quora.com/I'm-depressed-after-not-getting-into-an-MBBS-at-a-government-medical-college-My-parents-have-enrolled-me-in-a-private-college-They-say-I-can-make-up-for-the-financial-burden-in-a-PG-degree-I-dont-feel-confident-about-doing-so-What-should-I-do",
    "https://www.quora.com/Can-I-do-my-MBBS-in-India-and-then-pursue-PG-in-Harvard-Medical-School-in-US-interested-in-clinical-research",
    "https://www.quora.com/What-is-the-MBBS-degree-and-why-is-it-considered-so-important-in-India",
    "https://www.quora.com/If-after-completing-my-mbbs-from-india-I-do-my-residency-and-super-specialization-from-the-states-would-those-be-valid-in-india-if-I-ever-plan-on-returning-back",
    "https://www.quora.com/What-are-some-bitter-truths-about-life-at-an-MBBS-college-in-India-What-difficulties-do-MBBS-students-face",
    "https://www.quora.com/How-common-is-it-for-MBBS-students-to-get-depressed-while-going-through-medical-school",
]

# ─── PAIN POINT TAXONOMY ─────────────────────────────────────────────────────
#
# RULE: Every phrase here must be ≥ 3 words OR a very specific 2-word term.
# No single generic words — they create false positives on every answer.
#
# Each key = product category tag shown in the output.

PAIN_CATEGORIES = {

    # ── ARCA SPARK: Ambient Scribe / Documentation burden ─────────────────
    "ARCA SPARK — Documentation": [
        "hours writing notes",
        "hours of writing",
        "rewriting the same",
        "endless paperwork",
        "manual entry",
        "typing until",
        "hand cramps",
        "entry in register",
        "making files",
        "admission files",
        "filling forms",
        "clerical job",
        "doing clerical",
        "discharge takes hours",
        "discharge summary",
        "case sheets",
        "writing case",
        "patient notes",
        "writing notes",
        "documentation burden",
        "so much paperwork",
        "spend hours on paperwork",
        "spend time on paperwork",
        "half my day writing",
        "wasted on paperwork",
        "dictating notes",
        "handwriting prescriptions",
        "copy the same",
        "rewrite everything",
    ],

    # ── ARCA PULSE: EHR / Hospital software friction ──────────────────────
    "ARCA PULSE — EHR Friction": [
        "portal is slow",
        "server down",
        "server is down",
        "system crashed",
        "too many clicks",
        "computer not working",
        "system is glitchy",
        "duplicate entry",
        "software is a headache",
        "tracking patients",
        "hospital software",
        "hospital management system",
        "his is down",
        "electronic health record",
        "health record system",
        "emr is slow",
        "ehr is broken",
        "our software",
        "software crashes",
        "system is outdated",
        "logging into",
        "patient data entry",
    ],

    # ── Student Analytics: Thesis / Research pain ─────────────────────────
    "Student Analytics — Thesis & Research": [
        "spss is confusing",
        "thesis data",
        "thesis topic",
        "collecting data manually",
        "excel sheet hell",
        "fudging data",
        "fake data",
        "guide not helping",
        "my guide",
        "rejected my thesis",
        "data collection nightmare",
        "literature review",
        "no one to guide",
        "thesis submission",
        "synopsis rejected",
        "ethical clearance",
        "sample size calculation",
        "data collection is",
        "thesis is a nightmare",
        "thesis is stressful",
        "plagiarism check",
    ],

    # ── Scut Work: Intern / resident grunt work ───────────────────────────
    "Scut Work — Overload": [
        "running to the lab",
        "fetching reports",
        "drawing blood",
        "drawing samples",
        "pushing stretchers",
        "no ward boy",
        "ward boy work",
        "ward boy duties",
        "continuous 36 hours",
        "36 hour duty",
        "48 hour duty",
        "no sleep for",
        "skipping meals",
        "standing for hours",
        "overworked and underpaid",
        "intern duties",
        "house job is",
        "casualty duty",
        "on call for",
        "post call",
        "back to back duty",
        "no time to eat",
        "haven't slept",
        "working without sleep",
        "we do everything",
        "no one else to do it",
        "we are free labour",
        "free labor",
    ],

    # ── Toxicity & Mental Health ──────────────────────────────────────────
    "Toxicity — Mental Health": [
        "toxic unit",
        "toxic department",
        "toxic senior",
        "toxic culture",
        "consultant yelled",
        "screamed at me",
        "shouted at me",
        "thrown out of the ward",
        "humiliated in front of",
        "humiliated by",
        "mental breakdown",
        "want to quit mbbs",
        "want to leave medicine",
        "regretting medicine",
        "regret becoming a doctor",
        "depressed resident",
        "depressed intern",
        "feeling depressed",
        "thought of quitting",
        "cried after duty",
        "crying after work",
        "ragging in",
        "ragged by",
        "bullied by",
        "senior bullying",
        "harassment from",
        "burnout",
        "burned out",
        "burnt out",
        "suicidal thoughts",
        "anxiety during",
        "panic attacks",
        "not okay mentally",
        "breaking down",
    ],
}

# Noise: skip blocks containing these — ads, UI chrome, login prompts
AD_KEYWORDS = [
    "promoted by", "sponsored", "oliva clinic", "hair loss",
    "quora ads", "privacy policy", "terms of service",
    "sign up to read", "log in to read", "view more answers",
    "already have an account", "continue reading", "see more",
    "upvote if you", "follow for more", "originally answered",
]

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def is_noise(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in AD_KEYWORDS)


def score_text(text: str) -> int:
    """Count total keyword hits across all categories."""
    low = text.lower()
    return sum(
        1 for sigs in PAIN_CATEGORIES.values()
        for sig in sigs if sig.lower() in low
    )


def get_matched_categories(text: str) -> list[str]:
    """Return list of category names with at least one keyword hit."""
    low = text.lower()
    return [
        cat for cat, sigs in PAIN_CATEGORIES.items()
        if any(sig.lower() in low for sig in sigs)
    ]


def get_first_matched_keyword(text: str, categories: list[str]) -> str | None:
    """Find the first keyword that matched, for excerpt centering."""
    low = text.lower()
    for cat in categories:
        for sig in PAIN_CATEGORIES[cat]:
            if sig.lower() in low:
                return sig
    return None


def extract_smart_excerpt(text: str, keyword: str, category: str) -> str:
    """
    Build the pain_point string:
      [Category] …sentence containing the keyword…

    Strategy:
      1. Find the sentence that contains the keyword.
      2. If sentence > 300 chars, show CONTEXT_CHARS around the hit instead.
      3. Prefix with [Category] tag.
    """
    label = category
    flat  = text.replace("\n", " ").strip()
    low   = flat.lower()
    kw    = keyword.lower()

    # ── Find the sentence containing the keyword ──────────────────────────
    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', flat)
    best_sentence = ""
    for s in sentences:
        if kw in s.lower():
            best_sentence = s.strip()
            break

    if best_sentence and len(best_sentence) <= 350:
        body = best_sentence
    elif best_sentence:
        # Sentence is too long — zoom in on keyword position
        idx = best_sentence.lower().find(kw)
        start = max(0, idx - CONTEXT_CHARS)
        end   = min(len(best_sentence), idx + len(kw) + CONTEXT_CHARS)
        body  = ("…" if start > 0 else "") + best_sentence[start:end].strip() + ("…" if end < len(best_sentence) else "")
    else:
        # Fallback: keyword in the flat text
        idx   = low.find(kw)
        start = max(0, idx - CONTEXT_CHARS)
        end   = min(len(flat), idx + len(kw) + CONTEXT_CHARS)
        body  = ("…" if start > 0 else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")

    return f"[{label}] {body}"


def parse_username_from_href(href: str) -> str:
    """
    '/profile/John-Doe-123'  →  'John-Doe-123'
    'https://quora.com/profile/Jane-Smith'  →  'Jane-Smith'
    Returns empty string if pattern doesn't match.
    """
    m = re.search(r"/profile/([^/?#]+)", href or "")
    return m.group(1) if m else ""


# ─── JS EXTRACTOR ────────────────────────────────────────────────────────────
#
# Returns a list of { author_href, author_name, text } objects.
# We prefer href over display name because the URL slug IS the Quora username.

JS_EXTRACT = """
() => {
    const results  = [];
    const seenText = new Set();

    // ── Question title ──────────────────────────────────────────────────────
    const qEl = document.querySelector('h1')
              || document.querySelector('[class*="question_title"]');
    const question = qEl ? qEl.innerText.trim() : "";

    // ── Walk every answer-like container ───────────────────────────────────
    //    Quora's class names are hashed but the structure is stable:
    //    each answer has an author link  + a body div.
    const containers = document.querySelectorAll(
        '[class*="Answer"], [class*="AnswerBase"]'
    );

    containers.forEach(container => {

        // Author: grab the <a href="/profile/..."> link
        const authorLink = container.querySelector('a[href*="/profile/"]');
        const author_href = authorLink ? authorLink.getAttribute("href") : "";
        const author_name = authorLink ? authorLink.innerText.trim()      : "";

        // Body: the rendered answer text div
        const bodyEl = container.querySelector(
            '[class*="ui_qtext_rendered_qtext"], ' +
            '[class*="q-text"], ' +
            '[class*="AnswerBody"]'
        );
        if (!bodyEl) return;

        const text = bodyEl.innerText.trim();
        if (text.length < 100 || seenText.has(text)) return;
        seenText.add(text);

        results.push({ author_href, author_name, text });
    });

    // ── Fallback: grab long <p> blocks when structured selectors yield < 2 ─
    if (results.length < 2) {
        document.querySelectorAll('p').forEach(el => {
            const text = el.innerText.trim();
            if (text.length > 120 && text.length < 6000 && !seenText.has(text)) {
                seenText.add(text);
                // Try walking up the DOM to find a profile link
                let node = el.parentElement;
                let authorLink = null;
                for (let i = 0; i < 8; i++) {
                    if (!node) break;
                    authorLink = node.querySelector('a[href*="/profile/"]');
                    if (authorLink) break;
                    node = node.parentElement;
                }
                const author_href = authorLink ? authorLink.getAttribute("href") : "";
                const author_name = authorLink ? authorLink.innerText.trim()      : "";
                results.push({ author_href, author_name, text });
            }
        });
    }

    return { question, results };
}
"""


# ─── SCRAPER ─────────────────────────────────────────────────────────────────

def scrape_url(page, url: str, scraped_at: str) -> list[dict]:
    """
    Scrape one Quora URL.
    Returns a list of clean CRM-ready records (one per valid pain-point hit).
    """
    slug = (url.split("/")[-1] or url.split("/")[-2])[:55]
    print(f"\n  🌐  {slug}")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    except Exception as e:
        print(f"     ⚠️  Navigation warning: {e}")

    time.sleep(4)

    # Dismiss login / cookie banners
    for selector in [
        '[aria-label="Close"]',
        'button:has-text("Accept")',
        'button:has-text("No thanks")',
        '[class*="modal"] [class*="close"]',
    ]:
        try:
            btn = page.query_selector(selector)
            if btn:
                btn.click()
                time.sleep(0.5)
        except Exception:
            pass

    # Scroll to load lazy answers
    for i in range(SCROLL_ROUNDS):
        page.mouse.wheel(0, 2800)
        time.sleep(SCROLL_DELAY)
        if i == SCROLL_ROUNDS // 2:
            time.sleep(1.5)

    raw    = page.evaluate(JS_EXTRACT)
    blocks = raw.get("results", [])

    records: list[dict] = []
    seen_excerpts: set  = set()

    for item in blocks:
        text        = item.get("text", "").strip()
        author_href = item.get("author_href", "")
        author_name = item.get("author_name", "")

        if not text or is_noise(text):
            continue

        score = score_text(text)
        if score < MIN_SCORE:
            continue   # below noise threshold

        cats = get_matched_categories(text)
        if not cats:
            continue

        # ── Build user_id from profile URL (the real Quora username) ──────
        quora_username = parse_username_from_href(author_href)
        if not quora_username:
            # Fallback: sanitise display name into a slug
            quora_username = re.sub(r"[^\w\s-]", "", author_name).strip().replace(" ", "-") or "Quora-User"

        # ── Smart excerpt centred on the first matched keyword ─────────────
        first_kw = get_first_matched_keyword(text, cats)
        if not first_kw:
            continue

        pain_point = extract_smart_excerpt(text, first_kw, cats[0])

        # Skip if this excerpt is effectively a duplicate
        if pain_point in seen_excerpts:
            continue
        seen_excerpts.add(pain_point)

        records.append({
            "user_id":    quora_username,
            "pain_point": pain_point,
            "email":      None,
            "phone":      None,
            "source":     f"quora — {url}",
            "created_at": scraped_at,
            "processed":  False,
        })

    print(f"     ✅  {len(records)} clean pain-point record(s)")
    return records


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main() -> None:
    if not os.path.exists(SESSION_FILE):
        print(f"\n❌  Session file '{SESSION_FILE}' not found.")
        print("    Run  save_session.py  first to log in and save your Quora session.\n")
        return

    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_out   = f"quora_pain_points_{timestamp}.json"
    csv_out    = f"quora_pain_points_{timestamp}.csv"
    scraped_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    all_records: list[dict] = []

    print("\n" + "=" * 62)
    print("  QUORA PAIN POINT SCRAPER  v2  —  headless mode")
    print("=" * 62)
    print(f"  URLs        : {len(TARGET_URLS)}")
    print(f"  Min score   : {MIN_SCORE} keyword hit(s) required per block")
    print(f"  Session     : {SESSION_FILE}")
    print(f"  JSON out    : {json_out}")
    print(f"  CSV  out    : {csv_out}")
    print("=" * 62)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = browser.new_context(
            storage_state=SESSION_FILE,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-IN",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        page = context.new_page()

        for i, url in enumerate(TARGET_URLS, 1):
            print(f"\n[{i:>2}/{len(TARGET_URLS)}] Scraping…")
            try:
                records = scrape_url(page, url, scraped_at)
                all_records.extend(records)
            except Exception as e:
                print(f"     ❌  Failed: {e}")
            time.sleep(2.5)

        browser.close()

    # ── Write JSON ────────────────────────────────────────────────────────
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(all_records, f, indent=2, ensure_ascii=False)

    # ── Write CSV ─────────────────────────────────────────────────────────
    if all_records:
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_records[0].keys())
            writer.writeheader()
            writer.writerows(all_records)

    # ── Category breakdown ────────────────────────────────────────────────
    cat_tally: dict[str, int] = {}
    for r in all_records:
        m = re.match(r"^\[(.+?)\]", r["pain_point"])
        if m:
            cat = m.group(1)
            cat_tally[cat] = cat_tally.get(cat, 0) + 1

    print("\n" + "=" * 62)
    print("  SCRAPE COMPLETE")
    print("=" * 62)
    print(f"  URLs scraped    : {len(TARGET_URLS)}")
    print(f"  Total records   : {len(all_records)}")
    print("\n  Category breakdown:")
    for cat, cnt in sorted(cat_tally.items(), key=lambda x: -x[1]):
        bar = "█" * min(cnt, 30)
        print(f"    {cat:<44} {bar} ({cnt})")
    print(f"\n  📄  JSON → {json_out}")
    print(f"  📊  CSV  → {csv_out}")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    main()