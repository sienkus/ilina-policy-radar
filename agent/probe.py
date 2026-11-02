"""python -m agent.probe  — time each model on the real triage and extraction prompts, thinking on vs off."""
import json, os, re, time
from datetime import date
import requests

from .analyze import EXTRACT, TOPICS, TRIAGE, TYPES
from .llm import URL
from .store import Store

MODELS = ["nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3.5-lightning-30b-a3b", "openai/gpt-oss-20b"]
key = os.environ["NVIDIA_API_KEY"]
profile = open("config/profile.md").read()
items = [i for i in Store().items.values() if i.get("title")][:15]
triage_user = "\n".join(f"{n}. [{i['source']}] {i['title']}" for n, i in enumerate(items, 1))
it = next(i for i in Store().items.values() if i.get("status") == "done")
extract_user = f"Source: {it['source']}\nTitle: {it['title']}\n\n{it.get('snippet') or it.get('summary', '')}"
extract_sys = EXTRACT.format(profile=profile, today=date.today(), types=TYPES, topics=TOPICS, threads="(none yet)")
print("triage batch:\n" + triage_user + "\n")

for m in MODELS:
    for mode, extra in [("default", {}), ("no-think", {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "low"})]:
        for task, sys_, user, mt in [("triage", TRIAGE.format(profile=profile), triage_user, 300),
                                     ("extract", extract_sys, extract_user, 900)]:
            t = time.monotonic()
            try:
                r = requests.post(URL, timeout=(15, 120), headers={"Authorization": f"Bearer {key}"},
                                  json={"model": m, "max_tokens": mt, "temperature": 0.1, **extra,
                                        "messages": [{"role": "system", "content": sys_}, {"role": "user", "content": user}]})
                dt = time.monotonic() - t
                if r.status_code != 200:
                    print(f"{m:40} {mode:8} {task:7} HTTP {r.status_code} {r.text[:100]}")
                    continue
                c = r.json()["choices"][0]
                text = re.sub(r"<think>.*?</think>", "", c["message"].get("content") or "", flags=re.S)
                js = re.search(r"\{.*\}", text, re.S)
                try:
                    out = json.loads(js.group(0)) if js else None
                except json.JSONDecodeError:
                    out = None
                u = r.json().get("usage", {})
                show = out.get("keep") if out and task == "triage" else (out.get("fit"), out.get("type")) if out else repr(text[:50])
                print(f"{m:40} {mode:8} {task:7} {dt:5.1f}s finish={c.get('finish_reason')} out_tokens={u.get('completion_tokens')} json={'yes' if out else 'NO'} {show}")
            except Exception as e:
                print(f"{m:40} {mode:8} {task:7} {type(e).__name__} after {time.monotonic() - t:.0f}s")
            time.sleep(2)
