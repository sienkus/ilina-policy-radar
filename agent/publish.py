"""Turn analysed items into the dashboard data, a calendar feed and a CSV."""
import csv, json, os, re, shutil
from datetime import date, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "public")
TYPES = {"call_for_input": "Call for input", "opportunity": "Opportunity", "event": "Event",
         "policy_change": "Policy change", "appointment": "Appointment", "publication": "Publication",
         "science": "Science & research", "sector_impact": "Sector impact", "news": "News"}
RANK = {t: n for n, t in enumerate(TYPES)}
ACTIONABLE = ("call_for_input", "opportunity")
REGION = {"africa continental", "africa regional", "au member states", "african union", "pan-african", "pan african",
          "sub-saharan africa", "african continent", "africa-wide"}


def _country(c):
    c = c.strip()
    return "Africa" if c.lower() in REGION else "Global" if c.lower() in {"worldwide", "international", "global"} else c


def _most(values, default=""):
    values = [v for v in values if v]
    return max(set(values), key=values.count) if values else default


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
    if e["type"] in ACTIONABLE:
        s += 5
    return min(100, s)


def entries(store, today, contacts=()):
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
            "organizers": union("organizers")[:6], "topics": union("topics"),
            "countries": sorted({_country(c) for i in items for c in (i.get("countries") or []) if c})[:6],
            "arena": _most([i.get("arena") for i in items], "Other"), "sector": _most([i.get("sector") for i in items]),
            "impact": max((i.get("impact") or 0) for i in items), "first": any(i.get("first") for i in items),
            # contact names live only in the CONTACTS secret and the private email, never in public files
            "contacts": [n for n in contacts if any(re.search(r"\b" + re.escape(n) + r"\b", " ".join(
                [i.get("title") or "", i.get("headline") or "", i.get("summary") or "", i.get("why") or "",
                 i.get("snippet") or "", *(i.get("organizers") or [])]), re.I) for i in items)],
            "allies": union("allies"),
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
        if e["event_start"] and e["type"] in ("event", *ACTIONABLE) and e["score"] >= 50:
            ev(e["id"] + "-ev", date.fromisoformat(e["event_start"]), e["title"], desc, url)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


COLUMNS = ["Priority", "New", "Deadline", "Days left", "Title", "Category", "Impact", "Country", "Institution", "Sector",
           "Event date", "Location", "Next step", "Why it matters", "Organisers", "Score", "Sources",
           "First seen"]
WIDTHS = [9, 6, 12, 9, 50, 16, 9, 18, 18, 12, 12, 18, 45, 45, 30, 7, 8, 12]
DATES = [i + 1 for i, c in enumerate(COLUMNS) if c in ("Deadline", "Event date", "First seen")]
URGENT_DAYS = 21


def urgent(e, today):
    """Needs her action soon: a real chance to take part, a good fit, and a deadline in the next 3 weeks."""
    dl = _d(e["deadline"])
    return bool(dl and 0 <= (dl - today).days <= URGENT_DAYS and e["score"] >= 60
                and (e["type"] in ACTIONABLE or e["african_input"]))


def _priority(e, today):
    return "Urgent" if urgent(e, today) else "High" if e["score"] >= 70 else "Medium" if e["score"] >= 50 else "Low"


def _row(e, today):
    dl, ev = _d(e["deadline"]), _d(e["event_start"])
    new = "New" if e["found"] >= (today - timedelta(days=7)).isoformat() else ""
    cat = TYPES.get(e["type"], "News") + (" · First" if e["first"] else "")
    impact = "Major" if e["impact"] >= 4 else "Notable" if e["impact"] == 3 else ""
    return [_priority(e, today), new, dl, (dl - today).days if dl else None, e["title"], cat, impact,
            "; ".join(e["countries"]), e["arena"], e["sector"], ev, e["location"], e["action"], e["why"],
            "; ".join(e["organizers"]), e["score"], len(e["sources"]), _d(e["found"])]


def _sheet_order(es, today):
    """Urgent first, soonest deadline on top; then everything else by score."""
    return sorted(es, key=lambda e: (not urgent(e, today), e["deadline"] or "9999" if urgent(e, today) else "", -e["score"]))


def _grouped(es, key):
    """One row per (group, entry); an entry about Kenya and Nigeria shows up under both."""
    rows = [(g, e) for e in es for g in (key(e) or ["Unspecified"])]
    size = {}
    for g, _ in rows:
        size[g] = size.get(g, 0) + 1
    return sorted(rows, key=lambda r: (-size[r[0]], r[0]))  # biggest groups first, entry order kept within


def _sheets(es, today):
    """radar.xlsx for people (real dates, short links, colours, grouped tabs); CSVs for Google Sheets' IMPORTDATA."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    es = _sheet_order(es, today)
    views = [("Radar", None, [(None, e) for e in es]),
             ("By country", "Country group", _grouped(es, lambda e: e["countries"])),
             ("By institution", "Institution group", _grouped(es, lambda e: [e["arena"]]))]
    plain = lambda v: v.isoformat() if isinstance(v, date) else "" if v is None else v
    for name, (_, head, rows) in zip(["radar", "radar-by-country", "radar-by-institution"], views):
        with open(os.path.join(OUT, f"{name}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(([head] if head else []) + COLUMNS + ["Link"])
            for g, e in rows:
                w.writerow(([g] if head else []) + [plain(v) for v in _row(e, today)] + [e["sources"][0]["url"]])

    wb = Workbook()
    wb.remove(wb.active)
    fill = lambda c: PatternFill("solid", fgColor=c)
    colour = {"Urgent": "FFFFFF", "High": "B3261E", "Medium": "A86B00"}
    for title, head, rows in views:
        ws = wb.create_sheet(title)
        off = 1 if head else 0
        ws.append(([head] if head else []) + COLUMNS)
        for c in ws[1]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), fill("1F5F4A")
        last = None
        for g, e in rows:
            ws.append(([g if g != last else ""] if head else []) + _row(e, today))
            r = ws.max_row
            if head and g != last:
                ws.cell(r, 1).font = Font(bold=True)
                last = g
            t = ws.cell(r, 5 + off)
            t.hyperlink, t.font = e["sources"][0]["url"], Font(color="1F5F4A", underline="single")
            for col in DATES:
                ws.cell(r, col + off).number_format = "d mmm yyyy"
            days = ws.cell(r, 4 + off).value
            if days is not None:
                ws.cell(r, 4 + off).fill = fill("F4C7C3" if days < 14 else "FCE8B2" if days < 30 else "D9EAD3")
            p = ws.cell(r, 1 + off)
            p.font = Font(bold=True, color=colour.get(p.value, "6B6A64"))
            if p.value == "Urgent":
                p.fill = fill("B3261E")
            for c in ws[r]:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        for i, width in enumerate(([18] if head else []) + WIDTHS):
            ws.column_dimensions[get_column_letter(i + 1)].width = width
        ws.freeze_panes = ws.cell(2, 6 + off)  # group, priority, dates and title stay visible while scrolling
        ws.auto_filter.ref = ws.dimensions
    wb.save(os.path.join(OUT, "radar.xlsx"))


def build(store, contacts=()):
    """Writes the public files without contact names; returns full entries for the private email."""
    today = date.today()
    es = entries(store, today, contacts)
    public = [{k: v for k, v in e.items() if k != "contacts"} for e in es]
    os.makedirs(OUT, exist_ok=True)
    shutil.copy(os.path.join(os.path.dirname(__file__), "site", "index.html"), OUT)
    with open(os.path.join(OUT, "data.json"), "w") as f:
        json.dump({"generated": today.isoformat(), "entries": public}, f, ensure_ascii=False)
    with open(os.path.join(OUT, "deadlines.ics"), "w") as f:
        f.write(_ics(es, today))
    _sheets(es, today)
    print(f"  published {len(es)} entries")
    return es
