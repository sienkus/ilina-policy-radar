"""Optional digest by email and/or Slack. Daily: only new high-priority items. Weekly: the full roundup."""
import html, json, os, smtplib
from datetime import date, timedelta
from email.mime.text import MIMEText

import requests


def _site():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"]
    if "/" in repo:
        owner, name = repo.split("/")
        return f"https://{owner}.github.io/{name}/"
    return ""


def _line(e):
    bits = [f"<b>{e['score']}</b>", f"<a href='{html.escape(e['sources'][0]['url'])}'>{html.escape(e['title'])}</a>"]
    if e["deadline"]:
        bits.append(f"⏰ {e['deadline']}")
    if e["contacts"]:
        bits.append("🤝 " + ", ".join(e["contacts"]))
    return " · ".join(bits) + f"<br><small>{html.escape(e['action'] or e['why'])}</small>"


def send(store, entries, weekday=0):
    smtp, slack = os.environ.get("SMTP_USER"), os.environ.get("SLACK_WEBHOOK_URL")
    if not (smtp or slack):
        return
    today = date.today()
    sent = set(store.meta.get("notified", []))
    weekly = today.weekday() == weekday and store.meta.get("last_digest") != today.isoformat()
    if weekly:
        week = (today - timedelta(days=7)).isoformat()
        picks = [e for e in entries if e["updated"] >= week or e["found"] >= week][:25]
        subject = f"ILINA policy radar: week of {today:%d %b}"
    else:
        picks = [e for e in entries if e["score"] >= 70 and e["id"] not in sent][:15]
        subject = f"ILINA policy radar: {len(picks)} new priority item(s)"
    if not picks:
        return
    site = _site()
    body = "".join(f"<p>{_line(e)}</p>" for e in picks) + (f"<p><a href='{site}'>Open the full radar</a></p>" if site else "")

    if smtp:
        msg = MIMEText(body, "html")
        msg["Subject"], msg["From"], msg["To"] = subject, smtp, os.environ.get("DIGEST_TO", smtp)
        with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), 465) as s:
            s.login(smtp, os.environ["SMTP_PASS"])
            s.sendmail(smtp, [a.strip() for a in msg["To"].split(",")], msg.as_string())
    if slack:
        text = f"*{subject}*\n" + "\n".join(
            f"• *{e['score']}* <{e['sources'][0]['url']}|{e['title']}>" + (f" ⏰ {e['deadline']}" if e["deadline"] else "")
            for e in picks) + (f"\n<{site}|Open the full radar>" if site else "")
        requests.post(slack, data=json.dumps({"text": text}), timeout=20)

    store.meta["notified"] = sorted(sent | {e["id"] for e in entries if e["score"] >= 70})
    if weekly:
        store.meta["last_digest"] = today.isoformat()
    print(f"  sent: {subject}")
