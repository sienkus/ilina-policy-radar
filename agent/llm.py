"""NVIDIA NIM client (OpenAI-compatible) with pacing, retries and model fallback."""
import json, os, re, time
import requests

URL = "https://integrate.api.nvidia.com/v1/chat/completions"


class Unavailable(Exception):
    """API unreachable or quota exhausted; caller should stop and resume next run."""


class LLM:
    def __init__(self, models, rpm):
        self.key = os.environ.get("NVIDIA_API_KEY")
        if not self.key:
            raise SystemExit("NVIDIA_API_KEY is not set")
        self.models = list(models)
        self.gap = 60.0 / rpm
        self.last = 0.0
        self.calls = 0

    def _post(self, body):
        wait = self.gap - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        self.calls += 1
        return requests.post(URL, json=body, timeout=120,
                             headers={"Authorization": f"Bearer {self.key}", "Accept": "application/json"})

    def chat(self, system, user, max_tokens=1200):
        delay = 5
        for attempt in range(8):
            if not self.models:
                raise Unavailable("no working model")
            body = {"model": self.models[0], "max_tokens": max_tokens, "temperature": 0.1,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            try:
                r = self._post(body)
            except requests.RequestException as e:
                print(f"  network error ({type(e).__name__}), retry in {delay}s")
                time.sleep(delay); delay = min(delay * 2, 300)
                continue
            if r.status_code == 200:
                msg = r.json()["choices"][0]["message"]
                return msg.get("content") or ""
            gone = r.status_code in (404, 410) or (r.status_code == 400 and re.search(r"not found|does not exist|unknown model", r.text, re.I))
            if gone:
                print(f"  model {self.models[0]} unavailable ({r.status_code}), trying next")
                self.models.pop(0)
                continue
            if r.status_code in (401, 403):
                raise Unavailable(f"auth error {r.status_code}: {r.text[:200]}")
            # 429 / 5xx / anything else: back off
            ra = r.headers.get("Retry-After")
            wait = float(ra) if ra and ra.replace(".", "").isdigit() else delay
            print(f"  HTTP {r.status_code}, retry in {wait:.0f}s")
            time.sleep(wait); delay = min(delay * 2, 300)
        raise Unavailable("too many failed attempts")

    def json(self, system, user, max_tokens=1200):
        for _ in range(2):
            text = self.chat(system, user, max_tokens)
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
            user += "\n\nReturn ONLY valid JSON."
        raise ValueError("model did not return JSON")
