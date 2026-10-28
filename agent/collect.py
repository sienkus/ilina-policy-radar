"""Gather candidate items from feeds, watched pages and Google News. No LLM calls here."""
import hashlib, re, time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin, urlparse

import feedparser, requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
AI = re.compile(r"\bAI\b|\bA\.I\.|\bLLMs?\b|\bGenAI\b")
AI_WORDS = re.compile(r"artificial intelligence|machine learning|algorithm|frontier model|generative|"
                      r"intelligence artificielle|digital (policy|governance|transformation)|data governance", re.I)


def uid(s):
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def mentions_ai(text):
    return bool(AI.search(text) or AI_WORDS.search(text))


def clean(html, limit=None):
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    return text[:limit] if limit else text


def get(url, timeout=25):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def page_text(html):
    s = BeautifulSoup(html, "html.parser")
    for t in s(["script", "style", "noscript", "nav", "footer", "header", "form", "svg"]):
        t.decompose()
    root = s.find("main") or s.find("article") or s.body or s
    lines = (re.sub(r"\s+", " ", l).strip() for l in root.get_text("\n").split("\n"))
    return "\n".join(l for l in lines if len(l) > 2)


def fetch_text(url, limit=7000):
    """Readable text of an article; empty string if it can't be fetched."""
    if "news.google.com" in url:
        return ""
    try:
        return page_text(get(url, 20).text)[:limit]
    except Exception:
        return ""


def _when(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return datetime(*t[:6], tzinfo=timezone.utc) if t else None


def from_feed(src, max_age):
    f = feedparser.parse(get(src["url"]).content)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)
    for e in f.entries:
        when = _when(e)
        if when and when < cutoff:
            continue
        title, summary = e.get("title", "").strip(), clean(e.get("summary"), 1500)
        if not title or not e.get("link"):
            continue
        if src.get("filter") and not mentions_ai(f"{title} {summary} {' '.join(t.get('term', '') for t in e.get('tags', []))}"):
            continue
        yield {"id": uid(e.link), "url": e.link, "title": title, "snippet": summary,
               "published": when.date().isoformat() if when else None, "source": src["name"], "group": src["group"]}


def from_search(src):
    q = quote(src["q"] + " when:14d")
    f = feedparser.parse(get(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en").content)
    for e in f.entries:
        when = _when(e)
        publisher = e.get("source", {}).get("title", "")
        title = e.title.removesuffix(f" - {publisher}").strip()
        # id from the title so the same story via different searches is stored once
        yield {"id": uid(re.sub(r"\W+", "", title.lower())), "url": e.link, "title": title,
               "snippet": "", "published": when.date().isoformat() if when else None,
               "source": f"{publisher} (via search)", "group": src["group"]}


def from_page(src, store):
    """New links on the page become items; a meaningful text change becomes a 'page update' item."""
    r = get(src["url"])
    text = page_text(r.text)
    host = urlparse(r.url).netloc
    soup = BeautifulSoup(r.text, "html.parser")
    links = {}
    for a in soup.find_all("a", href=True):
        label = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        href = urljoin(r.url, a["href"]).split("#")[0]
        if len(label) > 25 and urlparse(href).netloc == host and href.rstrip("/") != r.url.rstrip("/"):
            links[href] = label[:200]

    prev = store.pages.get(src["url"])
    store.pages[src["url"]] = {"text": text[:20000], "links": sorted(links)}
    today = datetime.now(timezone.utc).date().isoformat()
    base = {"source": src["name"], "group": src["group"], "published": today}

    if prev is None:  # first visit: read the page once, don't flood with every old link
        if len(text) > 200:
            yield {**base, "id": uid(src["url"] + today), "url": src["url"], "title": f"{src['name']}: current page",
                   "snippet": "", "body": text[:7000], "kind": "page"}
        return

    old = set(prev["text"].split("\n"))
    added = [l for l in text.split("\n") if l not in old and len(l) > 30]
    if sum(map(len, added)) > 250:
        yield {**base, "id": uid(src["url"] + today), "url": src["url"], "title": f"{src['name']}: page updated",
               "snippet": "", "body": "New or changed text on the page:\n" + "\n".join(added)[:7000], "kind": "page"}

    for href in set(links) - set(prev["links"]):
        if src.get("filter") and not mentions_ai(links[href]):
            continue
        yield {**base, "id": uid(href), "url": href, "title": links[href], "snippet": ""}


def collect(cfg, store):
    jobs = [("feed", s) for s in cfg["feeds"]] + [("page", s) for s in cfg["pages"]] + [("search", s) for s in cfg["searches"]]
    new = 0
    for kind, src in jobs:
        label = src.get("name") or src["q"][:50]
        try:
            if kind == "feed":
                items = list(from_feed(src, cfg["max_age_days"]))
            elif kind == "page":
                items = list(from_page(src, store))
            else:
                items = list(from_search(src))
        except Exception as e:
            print(f"  ! {label}: {type(e).__name__} {str(e)[:100]}")
            continue
        n = sum(store.add(i) for i in items)
        new += n
        print(f"  {label}: {n} new")
        store.save()
    return new
