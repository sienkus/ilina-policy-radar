"""python -m agent  — collect, analyse, publish. Safe to interrupt and re-run at any point."""
import os, sys
import yaml

from . import analyze, collect, notify, publish, social
from .llm import LLM
from .store import Store

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load_env():
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path):
            k, _, v = line.strip().partition("=")
            if k and not k.startswith("#"):
                os.environ.setdefault(k, v.strip().strip('"'))


def main():
    load_env()
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config", "config.yaml")))
    cfg["contacts"] = (cfg.get("contacts") or []) + [n.strip() for n in os.environ.get("CONTACTS", "").splitlines() if n.strip()]
    profile = open(os.path.join(ROOT, "config", "profile.md")).read()
    store = Store()
    steps = sys.argv[1:] or ["collect", "analyze", "publish"]

    if "collect" in steps:
        print("Collecting")
        n = collect.collect(cfg, store) + social.collect(cfg, store)
        print(f"  {n} new items")
    if "analyze" in steps and not os.environ.get("NVIDIA_API_KEY"):
        print("Skipping analysis: NVIDIA_API_KEY is not set")
    elif "analyze" in steps:
        print("Analysing")
        llm = LLM(cfg["models"], cfg["requests_per_minute"])
        analyze.run(llm, store, profile, cfg)
        print(f"  {llm.calls} API calls")
    if "publish" in steps:
        print("Publishing")
        store.prune()
        store.save()
        entries = publish.build(store)
        try:
            notify.send(store, entries, cfg["digest_weekday"])
            store.save()
        except Exception as e:
            print(f"  ! notification failed: {e}")


if __name__ == "__main__":
    main()
