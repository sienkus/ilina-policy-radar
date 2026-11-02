"""Optional digest by email and/or Slack. Daily: only new high-priority items. Weekly: the full roundup."""
import html, json, os, smtplib
from datetime import date, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from .publish import urgent


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
        act = [e for e in entries if urgent(e, today)]
        rest = [e for e in entries if not urgent(e, today) and e["score"] >= 50 and (e["found"] >= week or e["updated"] >= week)]
        picks = act + rest[:20]
        subject = f"ILINA policy radar, week of {today:%d %b}" + (f": {len(act)} need action" if act else "")
    else:  # between digests, only interrupt for something urgent she hasn't been told about
        picks = [e for e in entries if urgent(e, today) and e["id"] not in sent]
        subject = f"ILINA policy radar: {len(picks)} urgent deadline(s)"
    if not picks:
        return
    site = _site()
    act = [e for e in picks if urgent(e, today)]
    body = (("<h3>Needs action (deadline within 3 weeks)</h3>" + "".join(f"<p>{_line(e)}</p>" for e in act)) if act else "")
    body += (("<h3>New this week</h3>" + "".join(f"<p>{_line(e)}</p>" for e in picks if e not in act)) if len(picks) > len(act) else "") + (f"<p><a href='{site}'>Open the full radar</a></p>" if site else "")

    if smtp:
        msg = MIMEMultipart()
        msg.attach(MIMEText(f"<div style='font-family:sans-serif;font-size:14px;max-width:640px'>{body}</div>", "html"))
        sheet = os.path.join(os.path.dirname(__file__), "..", "public", "radar.xlsx")
        if os.path.exists(sheet):  # the full list as a spreadsheet; opens in Excel, Numbers or Google Sheets
            part = MIMEApplication(open(sheet, "rb").read(), Name=f"ILINA policy radar {today:%d %b %Y}.xlsx")
            part["Content-Disposition"] = f'attachment; filename="ILINA policy radar {today:%d %b %Y}.xlsx"'
            msg.attach(part)
        msg["Subject"], msg["From"], msg["To"] = subject, smtp, os.environ.get("DIGEST_TO", smtp)
        with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), 465) as s:
            s.login(smtp, os.environ["SMTP_PASS"])
            s.sendmail(smtp, [a.strip() for a in msg["To"].split(",")], msg.as_string())
    if slack:
        text = f"*{subject}*\n" + "\n".join(
            f"• *{e['score']}* <{e['sources'][0]['url']}|{e['title']}>" + (f" ⏰ {e['deadline']}" if e["deadline"] else "")
            for e in picks) + (f"\n<{site}|Open the full radar>" if site else "")
        requests.post(slack, data=json.dumps({"text": text}), timeout=20)

    store.meta["notified"] = sorted(sent | {e["id"] for e in entries if urgent(e, today)})
    if weekly:
        store.meta["last_digest"] = today.isoformat()
    print(f"  sent: {subject}")
