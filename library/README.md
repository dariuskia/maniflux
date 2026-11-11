# The library

Everything saved in the Obsidian vault's `content/` folder (articles, papers, books, films, music, videos and the rest) is one library, with one note per item and the same properties on every note. This folder holds the code that writes and fixes those notes. Maniflux, Link Ingest and the Web Clipper write notes in the same format.

## The note format

```yaml
---
title: Close-Up
url: https://letterboxd.com/film/close-up/
source: "tyler cowen mentioned"      # where you heard of it (optional)
creators:                             # who made it: authors, directors, artists
  - "[[people/Abbas Kiarostami|Abbas Kiarostami]]"
year: 1990
date-added: 2026-10-22T13:40:28-07:00
date-archived: 2026-10-22             # empty = not archived yet
type: film
form:                                 # optional detail within the type
imdb: tt0100234                       # IDs from open databases, when found
wikidata: Q498277
---

(anything you write)
```

**`type`** is one of:

| Type | Covers | `form` values |
|---|---|---|
| `writing` | essays, articles, news, reviews, newsletters, threads | essay, news, review, interview, newsletter, thread, manifesto, guide |
| `research` | papers, reports, preprints | paper, report, syllabus |
| `book` | anything published as a book | |
| `podcast` | podcast episodes and series | episode |
| `video` | talks, interviews, video essays, other online video | talk, interview, video essay |
| `film` | features, documentaries, shorts, skate videos | documentary, short |
| `show` | TV and web series | |
| `music` | albums, tracks, mixes, radio shows, performances | album, ep, track, mix, radio show, performance |
| `art` | artworks, exhibitions | |
| `project` | websites, tools, datasets, organisations, open calls | |

Writing about music, film or art is still `writing`; it connects to the work through `people/` links.

**Archiving:** `date-archived` is the date you first archived something, and stays the same if you archive it again. Empty means not archived yet. Things imported from other platforms come in archived, with the platform's date (when you liked it, watched it, read it), or the import date if there isn't one.

**Tags** are yours: no tool writes any (the type is in `type`, and everything here is a saved item anyway).

**Creators** (`creators`, on every note, for writing as much as films and music) are links into `people/` (one note per person, whatever they make). Bands, labels, studios, publishers and venues will go in their own folder when the first ones arrive.

**IDs** come from Open Library, Wikidata and MusicBrainz (later also TMDB and Spotify). They're how a later import finds an existing note instead of making a duplicate, and how a person who makes both music and films ends up as one note.

**File names:** saved notes keep the page title as their file name, and nothing renames them later (a rename made outside Obsidian would break the journal's links). Notes made by imports get `Title (Creator)`, since titles collide more often there.

## How a saved link becomes a note

Everything is worked out in one place, the **library service** on the personal server (`service.py`, in Docker next to Maniflux, reachable over Tailscale at `https://home.<tailnet>.ts.net:8444` with a token). Saving never waits for it.

1. **Saving is instant.**
   - **Link Ingest** sends the link and where you found it to the service, which answers at once. For YouTube it also sends the video's details, read on the device, since YouTube won't show its page to the server. If the link is already saved, Link Ingest links the existing note in the journal right away.
   - **Maniflux's Save** sends the item, with the article text Maniflux already has, and archives it.
   - **The Web Clipper** writes a bare note with `status: pending`.
   - If the service can't be reached (offline, Tailscale off), Link Ingest and Maniflux also leave a `status: pending` note.
2. **In the background** the service reads the page, asks the LLM for the type, `form`, creators (the people who made it or are its subject, not the channel or station), description and minutes, then looks up year, cover and IDs (books, films, shows, music) or a cover (videos, podcasts), and checks for duplicates: the same link, the same database ID, or the same title and creator. It writes the finished note with `status: ready`. A pending note is filled in once it has been still for 10 seconds. You can write in it meanwhile: the service only changes properties, and if your text arrives while it works, it starts over with your text included. A pending note can carry the page's text in `clip-text` (the Web Clipper's `{{content}}`, the page as you saw it, logged in, past any paywall): the server reads that instead of fetching the page, then removes it. Without it, a description already in the note is kept, since the server only sees a paywall on sites like the NYT.
3. **Process New Clips** (on the Mac) links a `ready` note in the journal under the day it was saved, and removes `status`. It never links a `pending` one.

**Duplicates** never become a second note. A save of something already in the library sets `relink: <day>` on the existing note, and Process New Clips links it again under that day. A pending note that turns out to be a duplicate gets `status: duplicate` and `duplicate-of`, and Process New Clips adds anything written in it (and where it was found) to the original, links the original again, and moves the duplicate to the trash.

**Names:** a note is named after its title when it's written. Nothing renames a note once anything can link to it; the only rename is a pending note saved offline, named after the site, which gets its real title before it's ready.

**The sweep:** every 30 minutes the service also checks older notes (`enrich.py`): books, films, shows and music with no ID or cover, videos and podcasts with no cover, and notes sharing a database ID (the newer gets `duplicate-of`, listed in `Duplicates.base`). Covers come from the databases first (Open Library, Cover Art Archive), then the note's link: the Letterboxd poster, the Goodreads cover, Bandcamp or Spotify art, a YouTube thumbnail. Each step is tried once per note; a step that *fails* (a database down, the LLM unreachable) is retried after 10 minutes, an hour, 6 hours and a day, then given up with a line in the log.

Its log: `ssh root@home 'cd /root/feed && docker compose logs -f library'`.

## Views

`Check out next.base` in the vault: cover cards of everything not archived yet, with views for films and shows, books, music, and videos and podcasts, plus a table of everything grouped by type. Archive something by filling in its `date-archived`.

`Archive.base`: everything archived, newest first: a table of recently archived items, cover cards for films and shows, books, music, and videos and podcasts, a table of writing and research, and everything grouped by type.

## Code

- `service.py`: the library service (above): the save endpoint, the queue, the watcher for pending notes. `Dockerfile` runs it on the server.
- `fill.py`: working out a note: reading the page, the LLM call, duplicates, writing.
- `enrich.py`: the 30-minute sweep of older notes (also runs on its own: `uv run library/enrich.py` is a dry run).
- `library.py`: the shared pieces. The LLM cleanup (a tidy title, type and creators from a messy clip), the Open Library and Wikidata lookups (only confident matches; facts never come from the LLM), and the note writer. Settings come from the feed repo's `.env` (`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`); the vault is `~/Documents/Obsidian Vault` unless `VAULT_PATH` says otherwise.
- `done/`: one-off scripts that already ran (on 2026-11-10), kept as a record. They refuse to run again. Their backups and plans are in `~/feed-backups`.

Run anything here with `uv run`, which installs what each script needs.
