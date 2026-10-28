# ILINA policy radar

Every morning at 7am Nairobi time, this reads about 60 sources on AI governance and safety and turns them into one page you can skim in a few minutes. It's built for the job that used to mean scrolling LinkedIn and X every day and still missing deadlines.

It runs on GitHub for free. Nobody's laptop has to be on.

## What you'll look at

The dashboard has a tab called Act now. That's the one to open first. It lists things ILINA can actually do something about, like a call for submissions, a panel application or a public consultation, with the closest deadline at the top. Anything due in under two weeks shows up in red.

The other tabs are Events, Policy tracker (new or changed AI laws and strategies) and Reading (things worth citing in memos). If several sources talk about the same thing, say the Global Dialogue, they're grouped into one card so you get the LinkedIn post, the press release and the website update in one place.

Star what matters and dismiss what doesn't. Your browser remembers this, so it won't follow you to another device.

There's also a calendar link at the top. Subscribe to it once and every deadline lands in your calendar, with a reminder two weeks before. If you'd rather work in a spreadsheet, the CSV link gives you the same data.

## How it decides what matters

A language model reads each item against `config/profile.md`, which is ILINA's priorities written in plain English, and scores how well it fits. Then a few fixed rules add points. African policy consultations always get a big boost. So does anything involving someone we already know (the `CONTACTS` secret, kept out of the repo so the list isn't public), a partner organisation, or a deadline that's still at least two weeks out.

If the scores feel off, change `profile.md`. Just write what you'd tell a new research assistant.

## Setting it up

1. Push this folder to a public GitHub repo. Free accounts get unlimited Pages sites, one per repo, so this won't touch an existing one.
2. In the repo, open Settings, then Secrets and variables, then Actions, and add `NVIDIA_API_KEY`.
3. In Settings, then Pages, set the source to GitHub Actions.
4. Open the Actions tab, pick radar, and click Run workflow. The dashboard shows up at `https://<user>.github.io/<repo>/`.

Add `CONTACTS` there too, with one name per line for each person ILINA already has a relationship with.

Everything else is optional and goes in the same Secrets page. Nothing you put there is visible in the repo or the logs.

For LinkedIn and X posts, make a free [Apify](https://apify.com) account and add its token as `APIFY_TOKEN`. The free plan gives $5 a month and this should use $1 to $2 of it. The agent checks how much has been spent before each run and stops at $4.50, so you won't get a bill. The profiles it follows are under `social:` in the config.

For an email digest, add `SMTP_USER` (a Gmail address), `SMTP_PASS` (a Gmail [app password](https://myaccount.google.com/apppasswords), not the real one) and `DIGEST_TO` (who gets it, comma separated). You'll get an email when something high priority comes in, plus a roundup every Monday. For Slack, add `SLACK_WEBHOOK_URL` instead.

## Changing what it watches

Sources, contacts and partner organisations all live in `config/config.yaml`. If you find an RSS feed for someone (rssfinder.app is good for this), add it under `feeds:`. For a site without a feed, add it under `pages:`. The agent will check it daily and flag new links and changed text, which is how it caught the Global Dialogue dates moving without an announcement.

To run it on your own machine, put `NVIDIA_API_KEY=...` in a `.env` file, then run `pip install -r requirements.txt` and `python -m agent`. The dashboard ends up in `public/`.

## When things go wrong

It saves its progress after every call to the model, and the workflow commits that progress even when a run fails. If the API rate limit kicks in or the connection drops, it waits and retries. If the API stays down, it stops and picks up from the same spot the next morning. A run also stops after 40 minutes and leaves the rest for the next day, so a big backlog never blows past the free limits.

## What it can't see

Some sites only load with JavaScript, like IASEAI and AI Safety Connect, so the agent can't read them directly. It catches them through news searches instead. The same goes for anyone on LinkedIn or X if you don't add an Apify token. And it can't read private newsletters that only arrive by email.
