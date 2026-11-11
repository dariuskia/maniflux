# The library

Everything saved in the Obsidian vault's `content/` folder (articles, papers, books, films, music, videos and the rest) is one library, with one note per item and the same properties on every note. This folder holds the library service, which writes those notes, and the code that fixes them.

## The note format

```yaml
---
title: Close-Up (1990)                # the page's own title, as the browser tab shows it
url: https://letterboxd.com/film/close-up/
source: tyler cowen mentioned         # how it reached you (optional)
creators:                             # who made it
  - "[[people/Abbas Kiarostami|Abbas Kiarostami]]"
via:                                  # the outlet that put it out (empty here: none beyond the creator)
published:
date-added: 2026-10-22T13:40:28-07:00
type: film
form:                                 # optional detail within the type
minutes: 98
description: A fiction-documentary hybrid about...
year: 1990
cover: https://a.ltrbxd.com/...       # for the cards in the Bases
imdb: tt0100234                       # IDs from open databases, when found
wikidata: Q498277
date-archived: 2026-10-22             # empty = not archived yet; always the last property
---

(anything you write)
```

**`type`** is one of:

| Type | Covers | `form` values |
|---|---|---|
| `writing` | essays, articles, news, reviews, newsletters, threads | essay, news, review, interview, newsletter, thread, guide |
| `research` | papers, reports, preprints | paper, report |
| `book` | anything published as a book | |
| `podcast` | podcast episodes and series | episode |
| `video` | talks, interviews, video essays, other online video | interview, talk, video essay |
| `film` | features, documentaries, shorts, skate videos | documentary, short |
| `show` | TV and web series | |
| `music` | albums, tracks, mixes, radio shows, performances | album, ep, track, mix, radio show, performance |
| `art` | artworks, exhibitions | |
| `project` | websites, tools, datasets, organisations' pages, open calls | |

Writing about music, film or art is still `writing`; it connects to the work through `creators`.

**Who and where:** three properties answer three questions.

- **`creators`: who made it.** People whenever anyone is named: the author, director, artist, the DJ performing (not the station), the person interviewed. Organisations only when no person is credited (a report credited to a company; an article by several organisations with no author). People link into `people/`, organisations into `orgs/`.
- **`via`: the outlet that put it out**, linked into `orgs/`: the publication, the company (for its own blog), the newsletter, podcast, YouTube channel, radio station, record label or publishing house. Never the platform it's hosted on (Substack, YouTube, Bandcamp, Spotify, arXiv…), since the link already says that: on a platform it's the newsletter, channel, show or label behind it. Empty when the creator put it out under their own name (a personal blog or newsletter, their own channel, a self-released album), since it would only repeat the creator; an outlet with a name of its own still counts.
- **`source`: how it reached you**, in your words ("aaron sent it"), or the feed it came through in Maniflux.

| Example | creators | via |
|---|---|---|
| An engineer's post on the company blog | the engineer | the company |
| A report credited only to a company | the company | the company |
| A set on a radio station's YouTube channel | the DJ | the station |
| An album on Bandcamp | the artist | the label |
| A one-person Substack under the writer's name | the writer | (empty) |

`people/` has one note per person, whatever they make; `orgs/` one per organisation, whether it appears as a creator or a via. Their backlinks are everything by that person, or from that outlet.

**Archiving:** `date-archived` is the date you first archived something, and stays the same if you archive it again. Empty means not archived yet. Things imported from other platforms come in archived, with the platform's date (when you liked it, watched it, read it), or the import date if there isn't one.

**Tags** are yours: no tool writes any.

**IDs** come from Open Library, Wikidata and MusicBrainz (later also TMDB and Spotify). With the link, they're how a later save or import finds an existing note instead of making a duplicate.

**Titles and file names:** a note's title is the page's own (as the browser tab shows it), or the title the save brought (the Web Clipper's, Maniflux's item, YouTube's video). Only when a database confirms a book, film, show or album is its clean title used instead. The file is named after the title when the note is written. Rename notes in Obsidian, which keeps links intact; the server never renames anything.

## How a saved link becomes a note

Everything is worked out in one place, the **library service** on the personal server (`service.py`, in Docker next to Maniflux, reachable over Tailscale at `https://home.<tailnet>.ts.net:8444` with a token). Saving never waits for it.

1. **Saving is instant.**
   - **Link Ingest** sends the link and where you found it to the service, which answers at once. For YouTube it also sends the video's details, read on the device, since YouTube won't show its page to the server. If the link is already saved, Link Ingest links the existing note in the journal right away.
   - **Maniflux's Save** sends the item, with the article text Maniflux already has, and archives it.
   - **The Web Clipper** writes a bare note with `status: pending`, with the page's text in `clip-text` (its `{{content}}`: the page as you saw it, logged in, past any paywall).
   - If the service can't be reached (offline, Tailscale off), Link Ingest and Maniflux save nothing and say so. Paste the link in the journal for now, with any notes under it, and save it once you're back online.
2. **In the background** the service reads the page (or the text it was given), asks the LLM for the type, `form`, creators, via, description and minutes, then looks up year, cover and IDs (books, films, shows, music) or a cover (videos, podcasts), and checks for duplicates. It writes the finished note with `status: ready`. A pending note is filled in once it has been still for 10 seconds; you can write in it meanwhile, since the service only changes properties and starts over if your text arrives while it works. It removes `clip-text` when it's done. A description a pending note already had is kept when there's no `clip-text`.
3. **Process New Clips** (on the Mac) links a `ready` note under the day it was saved, at the top of that day's links, and removes `status`. It never links a `pending` note.

**Duplicates** never become a second note. A save is a duplicate when it has the same link (any spelling: `www.`, tracking and share bits, a start time, `youtu.be` or the mobile site, Spotify's country), the same database ID, or the same title and creator. Then the existing note gets `relink: <day>`, and Process New Clips links it again at the top of that day's links. A pending note that turns out to be a duplicate gets `status: duplicate` and `duplicate-of`; Process New Clips adds anything written in it (and where it was found) to the original, links the original again, and moves the duplicate to the trash.

**Saves the service couldn't finish** (the model unreachable, say) are kept as a pending note and retried. If that note had no title yet and was named after the site, the service sets `rename-to` once it knows the real title, and Process New Clips renames the note in Obsidian: your writing stays, it stays open if you're in it, and every link to it follows.

**The sweep:** every 30 minutes the service also checks older notes (`enrich.py`): books, films, shows and music with no ID or cover, videos and podcasts with no cover, and notes sharing a database ID (the newer gets `duplicate-of`, listed in `Duplicates.base`). Covers come from the databases first (Open Library, Cover Art Archive), then the note's link: the Letterboxd poster, the Goodreads cover, Bandcamp or Spotify art, a YouTube thumbnail. Each step is tried once per note; a step that *fails* (a database down, the LLM unreachable) is retried after 10 minutes, an hour, 6 hours and a day, then given up with a line in the log.

Its log: `ssh root@home 'cd /root/feed && docker compose logs -f library'`.

## Views

`Check out next.base` in the vault: cover cards of everything not archived yet, with views for films and shows, books, music, and videos and podcasts, plus a table of everything grouped by type. Archive something by filling in its `date-archived`.

`Archive.base`: everything archived, newest first, in the same views. `Duplicates.base`: notes flagged as possible duplicates.

## Code

- `service.py`: the library service: the save endpoint, the queue, the watcher for pending notes. `Dockerfile` runs it on the server; its token is `LIBRARY_TOKEN` in the server's `.env`.
- `fill.py`: working out a note: reading the page, the LLM call, duplicates, writing.
- `enrich.py`: the 30-minute sweep of older notes (also runs on its own: `uv run library/enrich.py` is a dry run).
- `library.py`: the shared pieces: settings, reading and editing note properties, the Open Library, Wikidata and MusicBrainz lookups (only confident matches; facts never come from the LLM), covers.
- `done/`: one-off scripts that already ran, kept as a record. They refuse to run again. Their backups and plans are in `~/feed-backups`.

Run anything here with `uv run`, which installs what each script needs.
