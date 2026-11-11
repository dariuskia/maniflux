# The library

Everything saved in the Obsidian vault's `content/` folder (articles, papers, books, films, music, videos and the rest) is one library, with one note per item and the same properties on every note. This folder holds the code that writes and fixes those notes. Maniflux, Link Ingest and the Web Clipper write notes in the same format.

## The note format

```yaml
---
title: Close-Up
url: https://letterboxd.com/film/close-up/
source: "tyler cowen mentioned"      # where you heard of it (optional)
creators:                             # or author:, on notes saved as clips
  - "[[people/Abbas Kiarostami|Abbas Kiarostami]]"
year: 1990
date-added: 2026-10-22T13:40:28-07:00
date-archived: 2026-10-22             # empty = not archived yet
type: film
form:                                 # optional detail within the type
imdb: tt0100234                       # IDs from open databases, when found
wikidata: Q498277
tags:
  - film                              # the type, plus favorite if it is one
---

## Notes
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

**Creators** are links into `people/` (one note per person, whatever they make). Bands, labels, studios, publishers and venues will go in their own folder when the first ones arrive.

**IDs** come from Open Library, Wikidata and (later) MusicBrainz, TMDB and Spotify. They're how a later import finds an existing note instead of making a duplicate, and how a person who makes both music and films ends up as one note.

**File names:** clips keep the title as the file name. Books, films and music get `Title (Creator)`, since their titles collide more often.

## Code

- `library.py`: the shared pieces. The LLM cleanup (a tidy title, type and creators from a messy clip), the Open Library and Wikidata lookups (only confident matches; facts never come from the LLM), and the note writer. Settings come from the feed repo's `.env` (`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`); the vault is `~/Documents/Obsidian Vault` unless `VAULT_PATH` says otherwise.
- `done/`: one-off scripts that already ran (on 2026-11-10), kept as a record. They refuse to run again. Their backups and plans are in `~/feed-backups`.

Run anything here with `uv run`, which installs what each script needs.
