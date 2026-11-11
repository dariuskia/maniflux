# Feed system

Darius's local, self-hosted system for following and triaging content (newsletters, Substacks, YouTube, later Instagram and music scenes), with Obsidian as the place for anything worth keeping.

## Layout

- `docker-compose.yml`, `.env` (private, never commit), `.env.example`: Miniflux + Postgres, Maniflux, the library service and ig2rss. `COMPOSE_PROFILES` picks what runs where: `reader` (Miniflux, Postgres, Maniflux, library) on the personal server, `ig2rss` on the Mac.
- `server/`: the personal server (the Kill the Newsletter Hetzner box, `home` in Tailscale): setup, moving from the Mac, deploys. Maniflux and Miniflux are served over Tailscale by Kill the Newsletter's web server; the vault copy is kept by Obsidian Headless. See `server/README.md`.
- `kill-the-newsletter/`: scripts for the self-hosted Kill the Newsletter server (Hetzner) that turns email newsletters into feeds.
- `docs/BRIEF.md`: the Maniflux brief: scope, features, Miniflux API calls, Obsidian note format, data, stack, decisions. **Read this before working on Maniflux.**
- `docs/SETUP-NOTES.md`: what's running, past decisions, what's not started. Private: it's in `.gitignore`, so keep server addresses and other personal details there, not in tracked files.
- `library/`: the vault's library (`content/`): the note format (type and form, creators and via, `date-archived`, IDs) and the library service that turns every saved link into a finished note. See `library/README.md`.
- `obsidian/`: reference copies of the QuickAdd scripts (Link Ingest, Process New Clips) and the Datacore dashboard in the vault. The live versions are in the vault's `_scripts/`; copy changes back here.

## Maniflux

Built from `docs/BRIEF.md` (read it before changing Maniflux). Key points:

- Python (FastAPI) + SQLite + server-rendered HTML with htmx. No frontend build step.
- Reaches Miniflux at `http://miniflux:8080/v1` with `MINIFLUX_API_KEY` in the `X-Auth-Token` header; served on `127.0.0.1:8090`.
- Thin design: never copy entries; read them live from Miniflux. SQLite holds only scores, weights, rules, feedback, saved-note paths and state.
- Save hands the item (with its text and your thought) to the library service and marks it read; it never waits for the model. The library service writes the note. If the service can't be reached, nothing is saved and the card stays. Linking in the journal happens in Obsidian (Process New Clips).
- Phone-friendly layout; keyboard shortcuts j/k/o/v/s/x.

## The vault

- Notes live in `content/`; the format is in `library/README.md`. The server never renames or deletes a note: only Obsidian does (Process New Clips), which keeps links intact.
- Changes to many notes go through a script with a dry run that saves its plan, a backup in `~/feed-backups`, and an apply step that carries out exactly that plan, then checks the result. One-offs go in `library/done/` and refuse to run again.

## Conventions

- Ports bind to `127.0.0.1` only. Nothing here should be reachable from the network except through Tailscale later.
- Secrets live only in `.env`. Never hard-code keys or print them.
- Test against the real Miniflux API shapes (Miniflux 2.3.x). Verify endpoints in Miniflux's source (`internal/api/api.go`) rather than from memory.
- Keep explanations and user-facing text plain and concise; no jargon or invented terminology.
- Run the tests before committing, and commit only when they pass; `server/deploy.sh` refuses uncommitted changes.
