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


COLUMNS = ["Priority", "New", "Deadline", "Days left", "Title", "Type", "Event date", "Location",
           "Next step", "Why it matters", "Organisers", "Warm contacts", "Score", "Sources", "First seen"]
WIDTHS = [9, 6, 12, 9, 50, 13, 12, 18, 45, 45, 30, 18, 7, 8, 12]
URGENT_DAYS = 21


def urgent(e, today):
    """Needs her action soon: a real chance to take part, a good fit, and a deadline in the next 3 weeks."""
    dl = _d(e["deadline"])
    return bool(dl and 0 <= (dl - today).days <= URGENT_DAYS and e["score"] >= 60
                and (e["type"] == "opportunity" or e["african_input"]))


def _priority(e, today):
    return "Urgent" if urgent(e, today) else "High" if e["score"] >= 70 else "Medium" if e["score"] >= 50 else "Low"


def _row(e, today):
    dl, ev = _d(e["deadline"]), _d(e["event_start"])
    new = "New" if e["found"] >= (today - timedelta(days=7)).isoformat() else ""
    return [_priority(e, today), new, dl, (dl - today).days if dl else None, e["title"],
            e["type"].replace("_", " ").capitalize(), ev, e["location"], e["action"], e["why"],
            "; ".join(e["organizers"]), "; ".join(e["contacts"]), e["score"], len(e["sources"]), _d(e["found"])]


def _sheet_order(es, today):
    """Urgent first, soonest deadline on top; then everything else by score."""
    return sorted(es, key=lambda e: (not urgent(e, today), e["deadline"] or "9999" if urgent(e, today) else "", -e["score"]))


def _sheets(es, today):
    """radar.xlsx for people (real dates, short links, colours); radar.csv for Google Sheets' IMPORTDATA."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    es = _sheet_order(es, today)
    with open(os.path.join(OUT, "radar.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS + ["Link"])
        for e in es:
            w.writerow([v.isoformat() if isinstance(v, date) else "" if v is None else v for v in _row(e, today)]
                       + [e["sources"][0]["url"]])

    wb = Workbook()
    ws = wb.active
    ws.title = f"Radar {today:%d %b}"
    ws.append(COLUMNS)
    fill = lambda c: PatternFill("solid", fgColor=c)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), fill("1F5F4A")
    colour = {"Urgent": "FFFFFF", "High": "B3261E", "Medium": "A86B00"}
    for e in es:
        ws.append(_row(e, today))
        r = ws.max_row
        title = ws.cell(r, 5)
        title.hyperlink, title.font = e["sources"][0]["url"], Font(color="1F5F4A", underline="single")
        for col in (3, 7, 15):
            ws.cell(r, col).number_format = "d mmm yyyy"
        days = ws.cell(r, 4).value
        if days is not None:
            ws.cell(r, 4).fill = fill("F4C7C3" if days < 14 else "FCE8B2" if days < 30 else "D9EAD3")
        p = ws.cell(r, 1)
        p.font = Font(bold=True, color=colour.get(p.value, "6B6A64"))
        if p.value == "Urgent":
            p.fill = fill("B3261E")
        for c in ws[r]:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for i, width in enumerate(WIDTHS):
        ws.column_dimensions[chr(65 + i)].width = width
    ws.freeze_panes = "F2"  # priority, dates and title stay visible while scrolling
    ws.auto_filter.ref = ws.dimensions
    wb.save(os.path.join(OUT, "radar.xlsx"))


def build(store):
    today = date.today()
    es = entries(store, today)
    os.makedirs(OUT, exist_ok=True)
    shutil.copy(os.path.join(os.path.dirname(__file__), "site", "index.html"), OUT)
    with open(os.path.join(OUT, "data.json"), "w") as f:
        json.dump({"generated": today.isoformat(), "entries": es}, f, ensure_ascii=False)
    with open(os.path.join(OUT, "deadlines.ics"), "w") as f:
        f.write(_ics(es, today))
    _sheets(es, today)
    print(f"  published {len(es)} entries")
    return es
