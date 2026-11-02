"""Two LLM passes: cheap batched triage on titles, then full extraction on what survives."""
import re, time
from datetime import date

from .collect import fetch_text, uid
from .llm import Unavailable

TYPES = ["opportunity", "event", "policy_change", "publication", "news"]
TOPICS = ["AI safety & security", "Global Dialogue / UN", "AI summits", "Africa policy", "public services",
          "health / agriculture / education", "evaluations & monitoring", "AI fund & capacity", "global AI law"]

TRIAGE = """You screen news for ILINA, an African AI safety and governance organisation.
{profile}

You get a numbered list of items. Keep an item only if it could plausibly matter to ILINA's priorities above:
AI governance, AI policy or law (especially in Africa), AI safety/security, multilateral AI processes,
events or opportunities to participate, or publications from AI safety / multilateral organisations.
Drop generic tech/business news, product launches and items unrelated to AI governance.
Return JSON: {{"keep": [numbers]}}"""

EXTRACT = """You analyse items for ILINA, an African AI safety and governance organisation.
{profile}

Today is {today}. Read the item and return JSON with exactly these keys:
{{
 "relevant": true/false,          // false if it does not matter to ILINA
 "type": one of {types},
   // opportunity = ONLY if the text names a concrete way for an outside organisation to take part now: a call for
   //   submissions, comments, input, applications, proposals, speakers or nominations, or open registration.
   //   Speeches, statements, partnerships, investments, launches and reports are NOT opportunities.
   // event = a specific dated convening; policy_change = a new or amended law, policy, strategy or regulation
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
 "countries": ["countries or regions concerned"],
 "topics": subset of {topics},
 "african_input": true/false,     // true ONLY if an African government, the AU or a regional body runs a process that is explicitly open to outside input (public participation, call for comments, stakeholder consultation, validation workshop). Policy news alone is false.
 "fit": 1-5,                      // 5 = squarely in ILINA's top priorities, 3 = useful context, 1 = marginal. Most news is 2-3.
 "thread": "the id (e.g. T1a2b3) of an ongoing thread below ONLY if this item is about that exact same event or process; else, if this item is itself an event, opportunity or policy process, a short name for it (e.g. 'UN Global Dialogue on AI Governance 2027'); else null"
}}
Only use dates stated or clearly implied in the text; never guess. Resolve relative dates using the publication date.

Ongoing threads:
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
    if kind not in ("event", "opportunity", "policy_change") or not norm:
        return None  # news and publications join threads but never start one
    k = "T" + uid(norm)[:5]
    store.threads[k] = {"name": name, "updated": date.today().isoformat()}
    return k


def triage(llm, store, profile, deadline_at):
    todo = [i for i in store.pending("pending") if i.get("kind") != "page"]
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


def _mentions(names, text):
    return [n for n in names if re.search(r"\b" + re.escape(n) + r"\b", text, re.I)]


def extract(llm, store, profile, cfg, deadline_at):
    todo = sorted(store.pending("triaged"), key=lambda i: i.get("published") or "", reverse=True)
    for n, i in enumerate(todo, 1):
        if time.monotonic() > deadline_at:
            print(f"  time budget reached, {len(todo) - n + 1} left for next run")
            return
        body = i.get("body") or fetch_text(i["url"]) or i.get("snippet", "")
        user = f"Source: {i['source']}\nPublished: {i.get('published')}\nURL: {i['url']}\nTitle: {i['title']}\n\n{body[:7000]}"
        system = EXTRACT.format(profile=profile, today=date.today(), types=TYPES, topics=TOPICS, threads=_threads_prompt(store))
        try:
            out = llm.json(system, user, 900)
        except ValueError:
            i["tries"] = i.get("tries", 0) + 1
            if i["tries"] >= 3:
                i["status"] = "skip"
            store.save()
            continue
        i.pop("body", None)
        if not out.get("relevant"):
            i["status"] = "skip"
        else:
            i.update({k: out.get(k) for k in ["type", "summary", "why", "action", "deadline", "event_start", "event_end",
                                              "location", "organizers", "participation", "countries", "topics",
                                              "african_input", "fit"]})
            i["headline"] = out.get("title") or i["title"]
            i["thread"] = _thread_id(store, out.get("thread"), i.get("type"), f"{i['title']} {body[:3000]}")
            text = f"{i['title']} {body} {' '.join(i.get('organizers') or [])}"
            i["contacts"] = _mentions(cfg["contacts"], text)
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
