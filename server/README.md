# The personal server

Miniflux, Maniflux and Kill the Newsletter run together on one Hetzner server, reached over Tailscale. ig2rss stays on the Mac, because Instagram is stricter with cloud servers.

```
Hetzner server ("home" in Tailscale)                         Mac
  Kill the Newsletter ── its web server also serves:            ig2rss ◄── Miniflux, over Tailscale
     https://home.<tailnet>.ts.net        → Maniflux            Obsidian ◄─┐
     https://home.<tailnet>.ts.net:8443   → Miniflux                       │ Obsidian Sync
  Miniflux + Postgres, Maniflux, library (Docker, this folder)                      │
  /srv/vault: copy of the vault ◄──────────────────────────────────────────┘
```

- **Kill the Newsletter** keeps ports 25, 80 and 443 for its own address, as before.
- **Maniflux and Miniflux** listen only on the server itself. Kill the Newsletter's web server passes requests on to them for the Tailscale address only, and refuses anyone not on your tailnet.
- **The vault copy** is kept current by Obsidian Headless (`ob`), with notes and attachments but no Obsidian settings or plugins (about 50 MB). Maniflux saves into its `content/` folder, and Obsidian Sync carries new notes to the Mac and phone, where Process New Clips links them in the journal as usual.
- **The library service** fills in year, cover and IDs on new book, film, show and music notes in the vault copy's `content/` folder; Obsidian Sync carries the changes to the Mac and phone. See `library/README.md`.
- **ig2rss** is shared on the Mac's Tailscale address (`tailscale serve`, port 8091). Miniflux fetches from it there, and gets errors on those feeds while the Mac sleeps, catching up when it wakes.

`COMPOSE_PROFILES` in `.env` decides what runs where: `reader` on the server, `ig2rss` on the Mac.

| Script | Where it runs | What it does |
|---|---|---|
| `setup.sh` | server | Installs Docker, Tailscale and Obsidian Headless, and syncs the vault copy |
| `move-from-mac.sh` | Mac, once | Moves Miniflux and Maniflux to the server and points Instagram feeds at the Mac |
| `serve-home.sh` | server | Adds the Tailscale addresses to Kill the Newsletter's web server (run by the move script) |
| `deploy.sh` | Mac | Copies code changes to the server and rebuilds |
| `backup-to-mac.sh` | Mac | Copies Miniflux's database and Maniflux's data to `~/feed-backups` |
| `install-backup.sh` | Mac, once | Runs `backup-to-mac.sh` every day at noon |

## Setting it up

1. **Resize the server.** In Hetzner Cloud: power off the Kill the Newsletter server, then Rescale to a type with 4 GB of memory and x86 (Kill the Newsletter has no ARM version). Choose **CPU and RAM only**, so the disk stays the same and you can scale back down later. Power it on. It keeps its IP, data and DNS records.
2. **Set it up.** From this folder on the Mac:
   ```sh
   scp server/setup.sh root@SERVER_IP:
   ssh -t root@SERVER_IP 'bash setup.sh "Personal"'
   ```
   The name is your vault's name in Obsidian Sync. It prints a Tailscale link to add the server as `home`, asks you to turn on HTTPS for the tailnet if it's off, and asks you to log in to Obsidian. The first vault sync takes a few minutes.
3. **In the Tailscale admin console,** open the `home` machine's menu and choose **Disable key expiry**, so it doesn't drop off after six months.
4. **Move Miniflux and Maniflux.** On the Mac, with Tailscale on:
   ```sh
   server/move-from-mac.sh root@home
   ```
   It stops them on the Mac first, so nothing is read or saved twice. It refuses to start if the server's vault copy is missing notes, or if the server already has a Miniflux database.
5. **Close public SSH.** Once `ssh root@home` works, remove port 22 from the server's Hetzner firewall. Hetzner's web console still works if you're ever locked out.
6. **On your phone,** with Tailscale on, open `https://home.<tailnet>.ts.net` and add it to the home screen.

## Day to day

- **Code changes:** commit, then `server/deploy.sh`. It copies code, never `.env` or data, and stops if anything is uncommitted (`--force` deploys anyway). Pushing to GitHub doesn't change the server.
- **Settings on the server:** `ssh root@home`, edit `/root/feed/.env`, then `cd /root/feed && docker compose up -d`.
- **Vault sync:** `ssh root@home journalctl -u obsidian-sync -f`, or `ob sync-status --path /srv/vault`.
- **Kill the Newsletter:** unchanged; see `kill-the-newsletter/README.md`. Its upgrade script keeps `configuration.mjs`, including the Tailscale addresses. If you reinstall it, run `bash serve-home.sh` on the server again.

## Backups

Every day at noon (or when the Mac next wakes), the Mac copies from the server to **`~/feed-backups`**:

- `miniflux-YYYY-MM-DD.sql.gz`: Miniflux's whole database (feeds, entries, read state, settings, API keys), about 11 MB.
- `maniflux-YYYY-MM-DD.db.gz`: Maniflux's SQLite file (scores, weights, rules, feedback, saved notes, digests), a few dozen KB.

The newest 14 of each are kept. The log is `~/feed-backups/backup.log`. It needs Tailscale on; a day the Mac is off or away is skipped.

- **Set it up** (once): `server/install-backup.sh`
- **Back up now:** `server/backup-to-mac.sh`
- **Not covered here:** the notes (they're in Obsidian Sync) and Kill the Newsletter (`kill-the-newsletter/backup-to-mac.sh`).

To restore onto the server (or a new one, after `setup.sh` and copying the code with `deploy.sh`):

```sh
F=~/feed-backups/miniflux-YYYY-MM-DD.sql.gz; M=~/feed-backups/maniflux-YYYY-MM-DD.db.gz
ssh root@home 'cd /root/feed && docker compose stop maniflux miniflux && docker compose up -d --wait db \
  && docker compose exec -T db psql -q -U miniflux -d postgres -c "DROP DATABASE miniflux" -c "CREATE DATABASE miniflux"'
gzip -dc "$F" | ssh root@home 'cd /root/feed && docker compose exec -T db psql -q -v ON_ERROR_STOP=1 -U miniflux -d miniflux'
gzip -dc "$M" | ssh root@home 'docker run --rm -i -v feed_maniflux-data:/data alpine sh -c "rm -f /data/maniflux.db-* && cat > /data/maniflux.db"'
ssh root@home 'cd /root/feed && docker compose up -d'
```

## Moving somewhere else

Everything personal is in this repo, `/root/feed/.env`, the `feed_miniflux-db` and `feed_maniflux-data` volumes, `/root/kill-the-newsletter/data/` and the Obsidian Sync login. A new machine needs `setup.sh`, Kill the Newsletter's `install.sh`, and the same volume copies the move script makes.
