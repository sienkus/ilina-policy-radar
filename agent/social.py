"""LinkedIn and X posts via Apify actors (optional; needs APIFY_TOKEN).

One batched actor run per network per day, only posts since the last successful run,
and a monthly spending cap so the free $5 Apify credit is never exceeded."""
import os, re
from datetime import date, datetime, timedelta, timezone

import requests

from .collect import uid

API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"


def _since(store, key, max_age):
    last = store.meta.get("social_since", {}).get(key)
    floor = (date.today() - timedelta(days=max_age)).isoformat()
    return max(last, floor) if last else (date.today() - timedelta(days=3)).isoformat()


def _run(actor, payload, token):
    r = requests.post(API.format(actor=actor.replace("/", "~")), params={"token": token},
                      json=payload, timeout=600)
    r.raise_for_status()
    return r.json()


def _linkedin(cfg, since, token):
    urls = [p["url"] for p in cfg["targets"] if p.get("url")]
    rows = _run(cfg["actor"], {"targetUrls": urls, "maxPosts": cfg.get("max_posts_per_profile", 5),
                               "postedLimitDate": since, "includeReposts": True,
                               "scrapeReactions": False, "scrapeComments": False}, token)
    for p in rows:
        text, url = p.get("content") or "", p.get("linkedinUrl")
        if not (text and url):
            continue
        yield {"url": url, "text": text, "author": (p.get("author") or {}).get("name", ""),
               "date": ((p.get("postedAt") or {}).get("date") or "")[:10] or None}


def _x(cfg, since, token):
    handles = [h["handle"].lstrip("@") for h in cfg["targets"] if h.get("handle")]
    ts = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
    # X search accepts long OR queries; chunk to stay under its length limit
    queries = [" OR ".join(f"from:{h}" for h in handles[i:i + 12]) for i in range(0, len(handles), 12)]
    rows = _run(cfg["actor"], {"searchTerms": queries, "since_time": ts, "queryType": "Latest",
                               "maxItems": cfg.get("max_items", 100)}, token)
    for t in rows:
        text, url = t.get("text") or "", t.get("url")
        if not (text and url) or t.get("type") == "mock_tweet":
            continue
        created = t.get("createdAt") or ""
        try:
            d = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y").date().isoformat()
        except ValueError:
            d = created[:10] or None
        yield {"url": url, "text": text, "author": (t.get("author") or {}).get("name", ""), "date": d}


def _used_usd(token):
    """Real Apify spend this billing cycle (covers runs from anywhere, not just this agent)."""
    r = requests.get("https://api.apify.com/v2/users/me/limits", params={"token": token}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["current"]["monthlyUsageUsd"]


def collect(cfg, store):
    token = os.environ.get("APIFY_TOKEN")
    sc = cfg.get("social") or {}
    if not token or not sc:
        return 0
    new = 0
    for key, fetch in (("linkedin", _linkedin), ("x", _x)):
        net = sc.get(key)
        if not net or not net.get("targets"):
            continue
        today = date.today().isoformat()
        try:
            used = _used_usd(token)
            if used >= sc.get("monthly_budget_usd", 4.5):
                print(f"  {key}: Apify budget reached (${used:.2f} used this month), skipping")
                continue
            posts = list(fetch(net, _since(store, key, cfg["max_age_days"]), token))
        except Exception as e:  # keep the old 'since' so the next run covers the gap
            print(f"  ! {key}: {type(e).__name__} {str(e)[:120]}")
            continue
        store.meta.setdefault("social_since", {})[key] = today
        label = "LinkedIn" if key == "linkedin" else "X"
        for p in posts:
            text = re.sub(r"\s+", " ", p["text"]).strip()
            new += store.add({"id": uid(p["url"]), "url": p["url"], "title": text[:140], "snippet": text[:1500],
                              "body": text, "published": p["date"], "source": f"{label} · {p['author']}",
                              "group": "social"})
        print(f"  {label}: {len(posts)} posts (Apify used before this run: ${used:.2f})")
        store.save()
    return new
