"""
scraper.py — Scrape OSRS news/update sources for price-affecting information.

Add your own sources to CUSTOM_SOURCES below, or pass them into scrape_all().
Each source dict needs:
    name             — display label
    url              — page to scrape
    article_selector — CSS selector for article containers
    title_selector   — CSS selector for headline (relative to article)
    link_selector    — CSS selector for <a> tag  (relative to article)
"""

import time
from typing import Dict, List

import requests
from bs4 import BeautifulSoup

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; OSRS-Tracker/1.0; "
        "+https://github.com/nardoneyland)"
    )
}

# ── Built-in sources ──────────────────────────────────────────────────────────
DEFAULT_SOURCES: List[Dict] = [
    {
        "name":             "OSRS Official News",
        "url":              "https://oldschool.runescape.com/news",
        "article_selector": ".news-article",
        "title_selector":   ".news-article__title",
        "link_selector":    "a",
    },
    {
        "name":             "Old School RuneScape Subreddit (RSS)",
        "url":              "https://www.reddit.com/r/2007scape/.rss",
        "article_selector": "entry",
        "title_selector":   "title",
        "link_selector":    "link",
    },
]

# ── Add your own sources here ─────────────────────────────────────────────────
CUSTOM_SOURCES: List[Dict] = [
    # Example:
    # {
    #     "name":             "GE Tracker Blog",
    #     "url":              "https://www.ge-tracker.com/blog",
    #     "article_selector": "article",
    #     "title_selector":   "h2",
    #     "link_selector":    "a",
    # },
]


# ── Core scrape logic ─────────────────────────────────────────────────────────

def _make_absolute(url: str, base_url: str) -> str:
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    scheme_host = "/".join(base_url.split("/")[:3])
    return scheme_host + ("" if url.startswith("/") else "/") + url


def scrape_source(source: Dict, max_articles: int = 5) -> List[Dict]:
    results = []
    try:
        r = requests.get(source["url"], headers=SCRAPE_HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        articles = soup.select(source["article_selector"])[:max_articles]
        for article in articles:
            title_el = article.select_one(source["title_selector"])
            link_el  = article.select_one(source["link_selector"])

            title = title_el.get_text(strip=True) if title_el else "No title"
            href  = (link_el.get("href") or link_el.get("src") or "") if link_el else ""
            url   = _make_absolute(href, source["url"]) if href else source["url"]

            # Grab readable preview text (strip scripts/styles first)
            for tag in article(["script", "style"]):
                tag.decompose()
            snippet = article.get_text(" ", strip=True)[:600]

            results.append({
                "title":   title,
                "url":     url,
                "source":  source["name"],
                "content": snippet,
            })

    except Exception as exc:
        print(f"[scraper] Could not scrape '{source['name']}': {exc}")

    return results


def scrape_all(extra_sources: List[Dict] = None, delay: float = 1.5) -> List[Dict]:
    """
    Scrape all sources and return a flat list of article dicts.
    Adds a polite delay between requests.
    """
    sources = DEFAULT_SOURCES + CUSTOM_SOURCES + (extra_sources or [])
    all_articles: List[Dict] = []
    for source in sources:
        all_articles.extend(scrape_source(source))
        time.sleep(delay)
    return all_articles
