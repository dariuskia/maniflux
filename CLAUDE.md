# Feed system

Darius's local, self-hosted system for following and triaging content (newsletters, Substacks, YouTube, later Instagram and music scenes), with Obsidian as the place for anything worth keeping.

## Layout

- `docker-compose.yml`, `.env` (private, never commit), `.env.example`: Miniflux + Postgres on the Mac at http://127.0.0.1:8080. Maniflux will be added here as another service.
- `kill-the-newsletter/`: scripts for the self-hosted Kill the Newsletter server (Hetzner) that turns email newsletters into feeds.
- `docs/BRIEF.md`: the Maniflux brief: scope, features, Miniflux API calls, Obsidian note format, data, stack, decisions. **Read this before working on Maniflux.**
- `docs/SETUP-NOTES.md`: what's running, past decisions, what's not started. Private: it's in `.gitignore`, so keep server addresses and other personal details there, not in tracked files.
- `obsidian/`: reference copies of the QuickAdd scripts and Datacore dashboard in the vault. Maniflux must write notes in the format they expect.

## Current task: build Maniflux version one

Follow `docs/BRIEF.md`. Key points:

- Python (FastAPI) + SQLite + server-rendered HTML with htmx. No frontend build step.
- New service in `docker-compose.yml`, published on `127.0.0.1:8090`, reaching Miniflux at `http://miniflux:8080/v1` with `MINIFLUX_API_KEY` in the `X-Auth-Token` header.
- Thin design: never copy entries; read them live from Miniflux. SQLite holds only scores, weights, rules, feedback, saved-note paths and state.
- Save writes a note to the vault's `content/` folder (mounted from `VAULT_CONTENT_PATH`) in the format in the brief, then marks the entry read in Miniflux. Skip URLs already in `content/`. Never edit an existing note. Linking in the journal happens in Obsidian (Process New Clips), not in Maniflux.
- On save, an item under 150 words uses its own text as `description` (no summary, no page fetch). OpenRouter (`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`) writes `description` for longer items, and picks `type` when the link doesn't make it clear. If that call fails, nothing is saved.
- Phone-friendly layout; keyboard shortcuts j/k/o/v/s/x.

## Conventions

- Ports bind to `127.0.0.1` only. Nothing here should be reachable from the network except through Tailscale later.
- Secrets live only in `.env`. Never hard-code keys or print them.
- Test against the real Miniflux API shapes (Miniflux 2.3.x). Verify endpoints in Miniflux's source (`internal/api/api.go`) rather than from memory.
- Keep explanations and user-facing text plain and concise; no jargon or invented terminology.
