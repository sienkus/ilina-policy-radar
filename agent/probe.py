"""python -m agent.probe  — time each candidate model on a small real task, to pick the default."""
import json, os, re, sys, time
import requests

from .llm import URL

CANDIDATES = sys.argv[1:] or [
    "deepseek-ai/deepseek-v4.1-flash", "nvidia/nemotron-3-super-120b-a12b", "google/gemma-4-31b-it",
    "moonshotai/kimi-k2.6", "z-ai/glm-5.3-flash", "openai/gpt-oss-20b", "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-nano-3-30b-a3b", "mistralai/mistral-large-2-instruct", "nvidia/nemotron-3-ultra-550b-a55b",
]
TASK = """You screen news for an African AI safety and governance organisation. Keep items about AI governance,
AI policy or law (especially in Africa), AI safety, multilateral AI processes, or chances to participate.
1. Kenya opens public participation on the draft Artificial Intelligence Bill, comments due 30 November
2. Safaricom shares rise after strong quarterly results
3. UN names co-chairs for 2027 Global Dialogue on AI Governance
4. New smartphone launched in Lagos with AI camera
Return JSON: {"keep": [numbers]}"""

key = os.environ["NVIDIA_API_KEY"]
for m in CANDIDATES:
    t = time.monotonic()
    try:
        r = requests.post(URL, timeout=(15, 90), headers={"Authorization": f"Bearer {key}"},
                          json={"model": m, "max_tokens": 600, "temperature": 0.1,
                                "messages": [{"role": "user", "content": TASK}]})
        dt = time.monotonic() - t
        if r.status_code != 200:
            print(f"{m:45} HTTP {r.status_code} {dt:5.1f}s {r.text[:80]}")
            continue
        text = r.json()["choices"][0]["message"].get("content") or ""
        ans = re.search(r"\{.*?\}", re.sub(r"<think>.*?</think>", "", text, flags=re.S), re.S)
        print(f"{m:45} OK  {dt:5.1f}s  {ans.group(0) if ans else 'NO JSON: ' + text[:60]!r}")
    except Exception as e:
        print(f"{m:45} {type(e).__name__} after {time.monotonic() - t:.0f}s")
    time.sleep(2)
