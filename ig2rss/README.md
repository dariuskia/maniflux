# ig2rss

Turns Instagram accounts into RSS feeds, one per account, using [Instaloader](https://instaloader.github.io). Miniflux subscribes to them, so posts show up in Maniflux like any other entry:

Instagram → ig2rss → Miniflux → Maniflux

ig2rss checks each account in the background, one at a time, about every 6 hours. It keeps what it read in its own SQLite file, and serves feeds from there. Opening a feed never contacts Instagram, and feeds keep working while Instagram is unreachable.

A check reads only posts newer than the newest one ig2rss already has, the way Instaloader's `--latest-stamps` does: it goes down the profile from the top, passes over the first 3 (they may be pinned), and stops at the first older post, reading at most 30. So nothing is missed after a gap, such as the Mac asleep for a few days. An account's first check reads its newest 6 posts (12 with a login). If an account ever had more than 3 pinned posts, its new posts would be missed; the same is true of Instaloader.

Each post becomes one item: the caption, up to 3 images (shrunk and put in the feed itself, so they don't expire like Instagram's image links), the post date, and a link to the post. Videos show their cover image and a link to watch on Instagram. Stories aren't included.

## Accounts

Add and remove accounts on ig2rss's page, http://127.0.0.1:8091. Type a username, `@name` or a profile link, and click Add. The account is read within a few minutes, and the page shows the feed address to add in Miniflux.

To look before adding, click Preview instead. ig2rss reads the account's latest posts from Instagram straight away (the same requests as one check, in the same queue) and shows them, with Follow and Discard buttons. Following keeps what the preview read, so it costs no extra requests. Previewing the same account again within an hour shows the saved preview. Previews you don't follow are deleted after a day. If ig2rss is waiting because Instagram refused it, a preview says so instead of asking Instagram.

Remove stops checking an account and deletes its saved posts. Its feed then answers "not found", so Miniflux shows an error until you unsubscribe there too.

## Settings

In `.env` next to `docker-compose.yml`, all optional:

```sh
IG2RSS_LOGIN=                           # your Instagram username, if you make a session file (below)
IG2RSS_POLL_HOURS=6                     # hours between checks of each account (at least 1)
IG2RSS_REELS=0                          # 1 = also read the Reels tab (one extra request per reel)
```

After changing them, run `docker compose up -d ig2rss`.

Reels shared to an account's grid already come through as posts. `IG2RSS_REELS=1` only matters for reels that aren't on the grid.

## Logging in

A login isn't required for public accounts, but it helps. Without one, Instaloader needs one request per post, so a check with nothing new costs about 6 requests, and each new post one more. With a login, posts come 12 per request, so a check usually takes about 2 requests, and private accounts you follow work too. Instaloader's docs also say logged-out use from cloud servers, VPNs and public proxies can get much stricter limits.

ig2rss never sees your password. It uses a session file that you make once:

```sh
docker compose build ig2rss
docker compose run --rm ig2rss instaloader --login YOUR_USERNAME --sessionfile /data/session
```

This asks for your password (and a 2FA code if you use one), then saves the session in the `ig2rss-data` volume. Set `IG2RSS_LOGIN=YOUR_USERNAME` in `.env` and run `docker compose up -d ig2rss`.

If Instagram blocks that login with a challenge, take the session from a browser where you're already logged in instead. On the Mac:

```sh
python3 -m venv /tmp/il && /tmp/il/bin/pip install 'instaloader[browser-cookie3]==4.16'
/tmp/il/bin/instaloader --load-cookies firefox --sessionfile ig-session   # or chrome, safari
docker compose cp ig-session ig2rss:/data/session && rm ig-session
```

A session file logs in as you, so keep it private. ig2rss notices a new one within a minute, with no restart. A separate Instagram account is better than your main one. Instaloader's docs say its request counting assumes nothing else (the website, the app, another Instaloader) is using Instagram at the same time, so don't use this account yourself while ig2rss runs.

## Adding a feed to Miniflux

In Miniflux (http://127.0.0.1:8080), add a subscription with:

```
http://ig2rss:8091/feeds/USERNAME.xml
```

That address works inside Docker, when Miniflux runs on the same machine. From a browser on this Mac, the same feed is at `http://127.0.0.1:8091/feeds/USERNAME.xml`.

With Miniflux on the personal server (see `server/README.md`), use the Mac's Tailscale address instead: `http://<Mac's Tailscale IP>:8091/feeds/USERNAME.xml`. `tailscale ip -4` on the Mac prints it.

The account must be on ig2rss's page. Its feed is empty until its first check, a few minutes after you add it. Miniflux picks up new posts on its own refresh, usually within an hour.

## Status

The same page shows each account's last successful read, its next check, any problem, and its feed addresses.

- **"Account not found"** or **"Private account…"**: only that account is affected. It's tried again next round.
- **"Instagram rejected the login…"**, **"Instagram asked for a login…"** or **"Instagram is blocking requests…"**: the session has expired or Instagram is refusing requests. All checks stop until the next round. Making a new session file starts them again straight away.
- **"Instagram wants to confirm it's you…"**: Instagram is asking whoever uses the account to confirm it (a "checkpoint"). Log into the account in a browser or the Instagram app, follow its steps, then make a new session file.

Instaloader paces its own requests and waits out "too many requests" answers. ig2rss adds a pause of 30 to 90 seconds between accounts.

## Development

```sh
cd ig2rss
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The tests use stand-ins for Instaloader and never contact Instagram.
