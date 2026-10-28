"""All agent state lives in data/state.json, written atomically after every change
so an interrupted run (rate limit, network, timeout) loses nothing."""
import json, os, tempfile
from datetime import date, timedelta

PATH = os.path.join(os.path.dirname(__file__), "..", "data", "state.json")


class Store:
    def __init__(self, path=PATH):
        self.path = path
        try:
            with open(path) as f:
                d = json.load(f)
        except FileNotFoundError:
            d = {}
        self.items = d.get("items", {})      # id -> item
        self.threads = d.get("threads", {})  # id -> {name, created}
        self.pages = d.get("pages", {})      # url -> {text, links}
        self.meta = d.get("meta", {})

    def save(self):
        d = {"meta": self.meta, "threads": self.threads, "items": self.items, "pages": self.pages}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path))
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, indent=1, sort_keys=True, ensure_ascii=False)
        os.replace(tmp, self.path)

    def add(self, item):
        if item["id"] in self.items:
            return False
        item.setdefault("status", "pending")
        item.setdefault("found", date.today().isoformat())
        self.items[item["id"]] = item
        return True

    def pending(self, status):
        return [i for i in self.items.values() if i["status"] == status]

    def prune(self, keep_days=120):
        """Shrink rejected/old items to a stub so the file stays small but they are never re-fetched."""
        cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
        for k, i in list(self.items.items()):
            dates = [i.get("found", ""), i.get("deadline") or "", i.get("event_end") or i.get("event_start") or ""]
            if i["status"] == "skip" and len(i) > 3:
                self.items[k] = {"id": k, "status": "skip", "found": i.get("found")}
            elif max(dates) < cutoff:
                del self.items[k]
