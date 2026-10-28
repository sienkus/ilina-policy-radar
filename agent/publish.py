"""Turn analysed items into the dashboard data, a calendar feed and a CSV."""
import csv, json, os, shutil
from datetime import date, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "public")
RANK = {"opportunity": 0, "event": 1, "policy_change": 2, "publication": 3, "news": 4}


def _d(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def score(e, today):
    """0-100. LLM fit drives it; the rules from ILINA's brief add on top."""
    s = (e["fit"] or 1) * 14
    if e["african_input"]:
        s += 20
    if e["contacts"]:
        s += 8
    if e["allies"]:
        s += 5
    dl = _d(e["deadline"])
    if dl and dl >= today + timedelta(days=14):
        s += 6
    if e["type"] == "opportunity":
        s += 5
    return min(100, s)


def entries(store, today):
    """One entry per thread (all sources about the same process in one place) or per standalone item."""
    groups = {}
    for i in store.items.values():
        if i["status"] == "done":
            groups.setdefault(i.get("thread") or i["id"], []).append(i)
    out = []
    for key, items in groups.items():
        items.sort(key=lambda i: (i.get("fit") or 0, i.get("published") or ""), reverse=True)
        lead = items[0]
        upcoming = sorted(d for i in items if (d := _d(i.get("deadline"))) and d >= today)
        starts = sorted(d for i in items if (d := _d(i.get("event_start"))) and d >= today - timedelta(days=3))
        ends = [d for i in items if (d := _d(i.get("event_end") or i.get("event_start")))]
        newest = max((i.get("published") or i["found"]) for i in items)
        last_date = max([*upcoming, *ends, _d(newest) or today])
        if last_date < today - timedelta(days=45):
            continue  # stale
        pick = lambda k: next((i[k] for i in items if i.get(k)), "")
        union = lambda k: sorted({x for i in items for x in (i.get(k) or []) if x})
        e = {
            "id": key,
            "title": store.threads[key]["name"] if key in store.threads and len(items) > 1 else lead.get("headline") or lead["title"],
            "type": min((i.get("type") or "news" for i in items), key=lambda t: RANK.get(t, 9)),
            "summary": lead.get("summary") or "", "why": pick("why"), "action": pick("action"),
            "participation": pick("participation"), "location": pick("location"),
            "deadline": upcoming[0].isoformat() if upcoming else None,
            "event_start": starts[0].isoformat() if starts else None,
            "organizers": union("organizers")[:6], "countries": union("countries")[:6], "topics": union("topics"),
            "contacts": union("contacts"), "allies": union("allies"),
            "african_input": any(i.get("african_input") for i in items),
            "fit": max((i.get("fit") or 1) for i in items),
            "found": min(i["found"] for i in items), "updated": newest,
            "sources": [{"title": i.get("headline") or i["title"], "url": i["url"], "source": i["source"],
                         "date": i.get("published") or i["found"]}
                        for i in sorted(items, key=lambda i: i.get("published") or "", reverse=True)][:12],
        }
        e["score"] = score(e, today)
        out.append(e)
    return sorted(out, key=lambda e: e["score"], reverse=True)


def _ics(entries, today):
    def esc(s):
        return str(s).replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ILINA policy radar//EN", "X-WR-CALNAME:ILINA policy deadlines"]
    def ev(uid, d, title, desc, url):
        lines.extend(["BEGIN:VEVENT", f"UID:{uid}@ilina-radar", f"DTSTAMP:{today:%Y%m%d}T000000Z",
                      f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                      f"SUMMARY:{esc(title)}", f"DESCRIPTION:{esc(desc)}", f"URL:{url}", "END:VEVENT"])
    for e in entries:
        url = e["sources"][0]["url"]
        desc = f"{e['summary']}\n{e['action']}\n{url}"
        if e["deadline"]:
            d = date.fromisoformat(e["deadline"])
            ev(e["id"] + "-dl", d, f"⏰ Deadline: {e['title']}", desc, url)
            if d - timedelta(days=14) >= today:
                ev(e["id"] + "-prep", d - timedelta(days=14), f"2 weeks to deadline: {e['title']}", desc, url)
        if e["event_start"] and e["type"] in ("event", "opportunity") and e["score"] >= 50:
            ev(e["id"] + "-ev", date.fromisoformat(e["event_start"]), e["title"], desc, url)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def build(store):
    today = date.today()
    es = entries(store, today)
    os.makedirs(OUT, exist_ok=True)
    shutil.copy(os.path.join(os.path.dirname(__file__), "site", "index.html"), OUT)
    with open(os.path.join(OUT, "data.json"), "w") as f:
        json.dump({"generated": today.isoformat(), "entries": es}, f, ensure_ascii=False)
    with open(os.path.join(OUT, "deadlines.ics"), "w") as f:
        f.write(_ics(es, today))
    with open(os.path.join(OUT, "radar.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Score", "Type", "Title", "Deadline", "Event date", "Location", "Action", "Why", "Organisers",
                    "Warm contacts", "Link", "Updated"])
        for e in es:
            w.writerow([e["score"], e["type"], e["title"], e["deadline"] or "", e["event_start"] or "", e["location"],
                        e["action"], e["why"], "; ".join(e["organizers"]), "; ".join(e["contacts"]),
                        e["sources"][0]["url"], e["updated"]])
    print(f"  published {len(es)} entries")
    return es
