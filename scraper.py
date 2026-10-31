"""
scraper.py — Scrape OSRS news/update sources for price-affecting information.

Add your own sources to SOURCES below, or pass them into scrape_all().
Each source dict needs:
    name             — display label
    url              — page to scrape (HTML or RSS/Atom feed)
    article_selector — CSS selector for article containers
    title_selector   — CSS selector for headline (relative to article)
    link_selector    — CSS selector for <a> tag  (relative to article)

Example HTML source (commented):
    {"name": "GE Tracker Blog",
     "url": "https://www.ge-tracker.com/blog",
     "article_selector": "article",
     "title_selector": "h2",
     "link_selector": "a"}
"""

import time
from typing import Dict, List

import requests
from bs4 import BeautifulSoup

import useragent

SCRAPE_HEADERS = useragent.HEADERS  # shared; per-install contact

SOURCES: List[Dict] = [
    {
        "name":             "OSRS Official News (RSS)",
        "url":              "https://secure.runescape.com/m=news/latest_news.rss?oldschool=1",
        "article_selector": "item",
        "title_selector":   "title",
        "link_selector":    "link",
    },
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
    useragent.require()
    results = []
    try:
        r = requests.get(source["url"], headers=SCRAPE_HEADERS, timeout=15)
        r.raise_for_status()
        # XML parser for RSS/Atom feeds, html.parser for HTML pages
        url_lower = source["url"].lower()
        is_feed = url_lower.endswith((".rss", ".xml")) or "rss" in url_lower
        parser = "xml" if is_feed else "html.parser"
        try:
            soup = BeautifulSoup(r.text, parser)
        except Exception:
            soup = BeautifulSoup(r.text, "html.parser")

        articles = soup.select(source["article_selector"])[:max_articles]
        for article in articles:
            title_el = article.select_one(source["title_selector"])
            link_el  = article.select_one(source["link_selector"])

            title = title_el.get_text(strip=True) if title_el else "No title"
            # Handle Atom <link href="..."/> AND RSS <link>url</link>
            href  = ""
            if link_el:
                href = (link_el.get("href")
                        or link_el.get("src")
                        or link_el.get_text(strip=True)
                        or "")
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

    except Exception:
        # Silent fail — caller treats empty result as "no articles".
        # In the bundled .app there's no terminal to receive prints anyway.
        pass

    return results


def scrape_all(extra_sources: List[Dict] = None, delay: float = 1.5) -> List[Dict]:
    """
    Scrape all sources and return a flat list of article dicts.
    Adds a polite delay between requests.
    """
    sources = SOURCES + (extra_sources or [])
    all_articles: List[Dict] = []
    for source in sources:
        all_articles.extend(scrape_source(source))
        time.sleep(delay)
    return all_articles
