"""Two LLM passes: cheap batched triage on titles, then full extraction on what survives."""
import re, time, unicodedata
from datetime import date

import requests

from .collect import fetch_text, uid
from .llm import Unavailable

TYPES = ["call_for_input", "opportunity", "event", "policy_change", "appointment", "publication", "science",
         "field", "sector_impact", "news"]
ARENAS = ["UN system", "African Union", "African regional bloc", "National government", "EU", "Other multilateral",
          "Industry", "Research & civil society", "Other"]
SCHEMA = 3  # bump when EXTRACT gains fields; older items get re-read
TOPICS = ["AI safety & security", "Global Dialogue / UN", "AI summits", "Africa policy", "public services",
          "health / agriculture / education", "evaluations & monitoring", "AI fund & capacity", "global AI law"]

TRIAGE = """You screen news for ILINA, an African AI safety and governance organisation.
{profile}

You get a numbered list of items. Keep an item if it could plausibly matter to ILINA's priorities above, OR if a policy researcher following AI
governance would want to know it: AI policy or law anywhere (especially Africa), AI safety/security, UN and other
multilateral AI work, events or calls to take part, publications, appointments to AI policy roles, firsts
(first law, ruling, institution or deployment of its kind), scientific breakthroughs with policy implications,
significant AI impacts in a sector (health, agriculture, education, finance, labour, security, justice), and the
state of the field: debates on AI governance, safety or technical questions, frontier lab announcements (model
releases, system cards, safety frameworks), benchmarks and capability evaluations, incident reports and
investigative reporting.
Drop consumer product launches, funding rounds, stock news and items unrelated to AI.
Return JSON: {{"keep": [numbers]}}"""

EXTRACT = """You analyse items for ILINA, an African AI safety and governance organisation.
{profile}

Today is {today}. Read the item and return JSON with exactly these keys. Every fact must come from the item's own
text. If the text is too short or vague to tell what it is about, return "relevant": false.
{{
 "relevant": true/false,          // false only if neither ILINA nor an AI policy researcher would care
 "type": one of {types},
   // call_for_input = an open call for submissions, comments, consultation responses or evidence
   // opportunity = another concrete way to take part now: applications, fellowships, funding calls, speakers,
   //   nominations or open registration. Speeches, partnerships, investments, launches and reports are NOT.
   //   call_for_input needs a named process that is open now; news about a bill or strategy is NOT a call.
   // event = a specific convening whose date is given in the text
   // policy_change = a government or intergovernmental body adopted, enacted, published, amended, withdrew or
   //   formally proposed a specific law, policy, strategy or regulation. Commentary, analysis or calls to act
   //   on a policy are NOT policy_change (use field).
   // appointment = someone appointed, elected or named to a role or expert body
   // publication = a report, paper, guidance or index from an institution
   // science = a new research finding or technical breakthrough with policy implications
   // field = state of the field: debates, commentary, op-eds and calls to action on AI governance or safety;
   //   frontier lab announcements (model releases, system cards, safety frameworks); benchmark and capability
   //   evaluation results; incident reports; investigative reporting
   // sector_impact = AI adoption or effects in a specific sector; news = anything else
 "title": "short clear title (max 12 words)",
 "summary": "what it is, max 2 plain sentences",
 "why": "why it matters to ILINA, 1 sentence",
 "action": "concrete next step for ILINA (e.g. 'Submit written input', 'Apply to speak', 'Cite in memo'), or empty",
 "deadline": "YYYY-MM-DD application/submission deadline or null",
 "event_start": "YYYY-MM-DD or null",
 "event_end": "YYYY-MM-DD or null",
 "location": "city, country / Online / empty",
 "organizers": ["organisations or people convening or publishing it"],
 "participation": "how to take part (modes, eligibility, link text), or empty",
 "countries": ["countries concerned, plain names e.g. 'Kenya'; use 'Africa' for continent-wide, 'Global' for worldwide"],
 "arena": one of {arenas},   // who is driving it
 "sector": "health / agriculture / education / finance / labour / security / justice / ... or empty",
 "impact": 1-5,                   // how big a deal this is for AI governance in general, ignoring ILINA's priorities. 5 = landmark, 3 = notable, 1 = routine
 "first": true/false,             // a first of its kind (first law, ruling, body, agreement or deployment of its kind somewhere)
 "topics": subset of {topics},
 "african_input": true/false,     // true ONLY if an African government, the AU or a regional body runs a process that is explicitly open to outside input (public participation, call for comments, stakeholder consultation, validation workshop). Policy news alone is false.
 "fit": 1-5,                      // 5 = squarely in ILINA's top priorities, 3 = useful context, 1 = marginal. Most news is 2-3.
 "thread": "the id (e.g. T1a2b3) of an ongoing thread below ONLY if this item is about that exact same event or process; else, if this item is itself an event, opportunity or policy process, a short name for it (e.g. 'UN Global Dialogue on AI Governance 2027'); else null"
}}
Only use dates stated or clearly implied in the text; never guess. Resolve relative dates using the publication date.

Ongoing threads (only for the "thread" key; never take facts from this list):
{threads}"""


def _threads_prompt(store):
    recent = sorted(store.threads.items(), key=lambda kv: kv[1].get("updated", ""), reverse=True)[:60]
    return "\n".join(f"{k}: {t['name']}" for k, t in recent) or "(none yet)"


GENERIC = {"africa", "african", "policy", "strategy", "governance", "artificial", "intelligence", "national",
           "digital", "safety", "with", "from", "2025", "2026", "2027", "2028"}


def _mentions_thread(name, text):
    """Code-side check on the model: the item must mention most of the thread's distinctive words."""
    words = {w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 4 and w not in GENERIC}
    text = text.lower()
    return not words or sum(w in text for w in words) >= max(1, round(len(words) * 2 / 3))


def _thread_id(store, value, kind, text=""):
    if not value:
        return None
    value = str(value).strip()
    m = re.search(r"\bT[0-9a-f]{5}\b", value)
    if m and m.group(0) in store.threads:  # the model sometimes writes "T1a2b3: name"
        if not _mentions_thread(store.threads[m.group(0)]["name"], text):
            return None  # the model attached it loosely; keep it as its own card
        store.threads[m.group(0)]["updated"] = date.today().isoformat()
        return m.group(0)
    name = re.sub(r"^(T[0-9a-f]{5}:?\s*)+", "", value).strip()
    norm = re.sub(r"\W+", " ", name.lower()).strip()
    for k, t in store.threads.items():
        if re.sub(r"\W+", " ", t["name"].lower()).strip() == norm:
            return k
    if kind not in ("event", "opportunity", "call_for_input", "policy_change") or not norm:
        return None  # news and publications join threads but never start one
    k = "T" + uid(norm)[:5]
    store.threads[k] = {"name": name, "updated": date.today().isoformat()}
    return k


def triage(llm, store, profile, deadline_at):
    todo = [i for i in store.pending("pending") if i.get("kind") != "page" and i.get("title")]
    for start in range(0, len(todo), 15):
        if time.monotonic() > deadline_at:
            return
        batch = todo[start:start + 15]
        lines = "\n".join(f"{n}. [{i['source']}] {i['title']} — {i.get('snippet', '')[:250]}" for n, i in enumerate(batch, 1))
        try:
            keep = {int(k) for k in llm.json(TRIAGE.format(profile=profile), lines, 300).get("keep", []) if str(k).isdigit()}
        except ValueError:  # unparseable twice: leave the batch for the next run, give up after 3 tries
            for i in batch:
                i["tries"] = i.get("tries", 0) + 1
                i["status"] = "triaged" if i["tries"] >= 3 else "pending"
            store.save()
            continue
        for n, i in enumerate(batch, 1):
            i["status"] = "triaged" if n in keep else "skip"
        store.save()
        print(f"  triage {start + len(batch)}/{len(todo)}: kept {len(keep)}")
    for i in store.pending("pending"):  # page snapshots skip triage
        if i.get("kind") == "page":
            i["status"] = "triaged"


SOCIAL = re.compile(r"^https?://(www\.)?(x\.com|twitter\.com|linkedin\.com)/", re.I)
STOP = {"with", "from", "that", "this", "will", "into", "over", "about", "their", "launch", "launches", "new"}


def _resolve(url):
    """Where a short link (t.co, lnkd.in, bit.ly) really points."""
    try:
        # plain client: t.co answers browsers with a page instead of a redirect
        return requests.head(url, headers={"User-Agent": "policy-radar"}, allow_redirects=True, timeout=15).url
    except requests.RequestException:
        return url


def _linked(i, body):
    """Social posts are often just a comment and a link: read the linked page too, and remember where it went."""
    if i.get("group") != "social":
        return body
    for short in re.findall(r"https?://\S+", body)[:2]:
        url = _resolve(short.rstrip(".,)"))
        if SOCIAL.match(url):
            continue  # a link to another post, nothing more to read
        i["link"] = url
        page = fetch_text(url, 4000)
        return body.replace(short, url) + (f"\n\nLinked page ({url}):\n{page}" if page else "")
    return body


def _words(text):
    text = unicodedata.normalize("NFKC", text)  # 𝐁𝐨𝐥𝐝 LinkedIn letters -> plain letters
    return re.findall(r"[a-z\u00c0-\u024f]{3,}", re.sub(r"https?://\S+", " ", text.lower()))


def _date_in(d, text):
    """Is this date actually written in the text (e.g. '12 March', 'March 12', '2026-03-12')?"""
    try:
        d = date.fromisoformat(str(d)[:10])
    except ValueError:
        return False
    t = unicodedata.normalize("NFKC", text).lower()
    month = rf"(?:{d:%B}|{d:%b})".lower()
    return bool(d.isoformat() in t or re.search(rf"\b{d.day}(?:st|nd|rd|th)?\s+{month}|{month}\.?\s+{d.day}\b", t))


def _grounded(headline, text):
    """Code-side check that the model described this item and didn't borrow a story from elsewhere."""
    key = {w for w in _words(headline) if len(w) >= 4 and w not in GENERIC and w not in STOP}
    have = set(_words(text))
    hits = sum(w in have or w[:-1] in have or w + "s" in have for w in key)
    return len(key) < 2 or hits >= max(1, len(key) // 3)


def _mentions(names, text):
    return [n for n in names if re.search(r"\b" + re.escape(n) + r"\b", text, re.I)]


def extract(llm, store, profile, cfg, deadline_at):
    new = sorted(store.pending("triaged"), key=lambda i: i.get("published") or "", reverse=True)
    old = sorted((i for i in store.pending("done") if i.get("v", 1) < SCHEMA), key=lambda i: i.get("published") or "", reverse=True)
    todo = new + old  # old items stay on the dashboard until re-read
    for n, i in enumerate(todo, 1):
        if time.monotonic() > deadline_at:
            print(f"  time budget reached, {len(todo) - n + 1} left for next run")
            return
        body = i.get("body") or fetch_text(i["url"]) or i.get("snippet", "")
        body = _linked(i, body)
        if i.get("group") == "social" and len(_words(body)) < 12:  # e.g. a tweet that is only a link we couldn't open
            i["status"], i["why_skipped"] = "skip", "too little text to read"
            i.pop("body", None)
            store.save()
            continue
        # Google News links can't be opened server-side, so those items are often just a headline
        i["headline_only"] = len(_words(body)) - len(_words(i["title"])) < 25
        note = ("\n\n(Only the headline is available. Leave dates, location and participation empty unless the "
                "headline states them.)" if i["headline_only"] else "")
        user = f"Source: {i['source']}\nPublished: {i.get('published')}\nURL: {i['url']}\nTitle: {i['title']}\n\n{body[:7000]}{note}"
        system = EXTRACT.format(profile=profile, today=date.today(), types=TYPES, topics=TOPICS, arenas=ARENAS, threads=_threads_prompt(store))
        try:
            out = llm.json(system, user, 900)
        except ValueError:
            i["tries"] = i.get("tries", 0) + 1
            if i["tries"] >= 3:
                if i["status"] == "done":
                    i["v"] = SCHEMA  # keep the old reading rather than lose the item
                else:
                    i["status"] = "skip"
            store.save()
            continue
        i.pop("body", None)
        if not out.get("relevant"):
            i["status"] = "skip"
        elif not _grounded(out.get("title") or "", f"{i['title']} {body}"):
            i["status"], i["why_skipped"] = "skip", f"headline not found in source: {out.get('title')}"
        else:
            i.update({k: out.get(k) for k in ["type", "summary", "why", "action", "deadline", "event_start", "event_end",
                                              "location", "organizers", "participation", "countries", "topics",
                                              "african_input", "fit", "arena", "sector", "impact", "first"]})
            if i["headline_only"]:  # the model can't know dates it wasn't shown; drop any it filled in
                seen = f"{i['title']} {body}"
                for k in ("deadline", "event_start", "event_end"):
                    if i.get(k) and not _date_in(i[k], seen):
                        i[k] = None
            i["v"] = SCHEMA
            i["headline"] = out.get("title") or i["title"]
            i["thread"] = _thread_id(store, out.get("thread"), i.get("type"), f"{i['title']} {body[:3000]}")
            text = f"{i['title']} {body} {' '.join(i.get('organizers') or [])}"
            i["allies"] = _mentions(cfg["allies"], text)
            i["status"] = "done"
        store.save()
        print(f"  [{n}/{len(todo)}] {'✓' if i['status'] == 'done' else '·'} {i['title'][:80]}")


def run(llm, store, profile, cfg):
    deadline_at = time.monotonic() + cfg["time_budget_minutes"] * 60
    try:
        triage(llm, store, profile, deadline_at)
        extract(llm, store, profile, cfg, deadline_at)
    except Unavailable as e:
        print(f"  LLM unavailable ({e}); progress saved, will resume next run")
