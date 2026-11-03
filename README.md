# Feed system

Miniflux running on your Mac, newsletters coming in through your own Kill the Newsletter server, and (soon) Maniflux, your own reading app on top.

| Folder / file | What it is |
|---|---|
| `docker-compose.yml`, `.env` | Miniflux and its database (Maniflux will join here) |
| `kill-the-newsletter/` | Server scripts for newsletters-to-feeds; see its README |
| `docs/BRIEF.md` | The Maniflux brief |
| `docs/SETUP-NOTES.md` | What's running and why on your own machines (local only, not in git) |
| `obsidian/` | Copies of the Obsidian scripts Maniflux works with |
| `CLAUDE.md` | Context for AI coding agents (Zed, Claude Code) |

## Start Miniflux

You need Docker Desktop.

```sh
cp .env.example .env      # then set the passwords in .env
docker compose up -d
```

Open http://127.0.0.1:8080 and log in with the admin user from `.env`.

## Add feeds

- **Substack:** `https://<name>.substack.com/feed` (or `<custom-domain>/feed`).
- **YouTube channel:** paste the channel's page URL; Miniflux finds its feed.
- **Most sites:** paste the site's URL; Miniflux finds the feed if there is one.
- **Newsletters with no feed:** `kill-the-newsletter/new-inbox.sh "Name"`, subscribe with the printed address, add the printed feed URL.

## Back up

```sh
docker compose exec -T db pg_dump -U miniflux miniflux > backup-$(date +%F).sql
```

## Later

- RSSHub and Maniflux get added to `docker-compose.yml`. Miniflux already allows fetching from and sending webhooks to local services.
- Phone access through Tailscale.
