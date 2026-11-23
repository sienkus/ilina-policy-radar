"""Her shortlist and watch list live in a Watchlist tab of the Google Sheet, reached through a small Apps Script
(sheets/watchlist.gs) that the dashboard writes to. The agent reads it to tell her when something she watches
gets news. The same script can send email from the sheet owner's Gmail, so no SMTP password is needed.

    python -m agent.watch link   prints the one-time setup link that connects a browser to the sheet
"""
import base64, os, re, sys

import requests

from .analyze import GENERIC, STOP


def _cfg():
    return os.environ.get("WATCH_URL"), os.environ.get("WATCH_KEY")


def enabled():
    return all(_cfg())


def rows():
    url, key = _cfg()
    r = requests.post(url, json={"key": key, "action": "list"}, timeout=60)
    r.raise_for_status()
    out = r.json()
    if "error" in out:
        raise RuntimeError(out["error"])
    return out.get("rows", [])


def _yes(v):
    return str(v).strip().lower() in ("true", "1", "yes", "y", "x")


def _distinct(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 4 and w not in GENERIC and w not in STOP}


def _about(row_title, terms, text):
    """Her own keywords win; otherwise the new item must share the watched title's distinctive words."""
    text = text.lower()
    if terms:
        return any(t.lower() in text for t in terms)
    words = _distinct(row_title)
    if len(words) < 2:
        return False  # too generic to match safely; she can add keywords in the sheet
    have = _distinct(text)
    return sum(w in have for w in words) >= (len(words) if len(words) <= 3 else round(len(words) * 2 / 3))


def updates(entries, store):
    """New sources about watched items since she started watching, each reported once.
    Returns [(watched title, entry, source)]."""
    if not enabled():
        return []
    seen = set(store.meta.get("watch_seen", []))
    out = []
    for r in rows():
        if not _yes(r.get("watch")) or not r.get("id"):
            continue
        since = str(r.get("watch_since") or r.get("added") or "")[:10]
        terms = [t.strip() for t in re.split(r"[;,\n]", str(r.get("keywords") or "")) if t.strip()]
        title = str(r.get("title") or "")
        for e in entries:
            same = e["id"] == r["id"]
            for s in e["sources"]:
                tag = f"{r['id']}|{s['url']}"
                if tag in seen or s["found"] <= since:
                    continue
                if same or _about(title, terms, f"{e['title']} {e['summary']} {s['title']}"):
                    out.append((title, e, s))
                    seen.add(tag)
    store.meta["watch_seen"] = sorted(seen)[-3000:]
    return out


def mail(subject, html, attachment=None):
    """Send through the sheet's Apps Script; it only ever mails the address set inside the script."""
    url, key = _cfg()
    body = {"key": key, "action": "mail", "subject": subject, "html": html}
    if attachment:
        name, path = attachment
        body["attachment"] = {"name": name, "b64": base64.b64encode(open(path, "rb").read()).decode()}
    r = requests.post(url, json=body, timeout=60)
    r.raise_for_status()
    if "error" in r.json():
        raise RuntimeError(r.json()["error"])


if __name__ == "__main__":
    if sys.argv[1:] == ["link"]:
        from .__main__ import load_env
        load_env()
        url, key = _cfg()
        if not (url and key):
            sys.exit("Set WATCH_URL and WATCH_KEY in .env first")
        site = os.environ.get("SITE_URL", "https://sienkus.github.io/ilina-policy-radar/")
        token = base64.urlsafe_b64encode(f"{url}|{key}".encode()).decode()
        print(f"{site}#sync={token}")
