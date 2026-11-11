# Maniflux Brief

Oct 30, 2026 · Darius Kianersi

Maniflux is a small app beside Miniflux that ranks what's new, lets you skim and steer it, and hands anything worth keeping to Obsidian, where your TBR and notes already live.

## Scope

Version one is a better place to skim than Miniflux, with saving to Obsidian and simple steering built in. It uses an LLM for two things: writing a saved note's type and description, and writing digests. LLM scoring and anything social come after it works day to day.

- **In:** a ranked list of unread items, a reading view, archive, save to Obsidian, per-source weights, rules with end dates, a time budget, keyboard shortcuts, a phone-friendly layout.
- **Out (for now):** LLM scoring and summaries in the list, story grouping, friends' picks, Instagram and Resident Advisor converters, learning from interests in Obsidian.
- **Unchanged:** Miniflux keeps fetching feeds and holds subscriptions, entries and read state. Obsidian keeps your TBR, notes and archive.

## How it fits

```
Sources ──feeds──► Miniflux ──entries──► Maniflux ──save──► Obsidian content/
(Substacks,        (fetches hourly,  ◄──read───  (ranks, filters,       (TBR dashboard,
 YouTube, email     stores entries,               rules, weights,        your notes,
 newsletters)       read/starred)                 time budget,           journal links)
                                                  scores in SQLite)            ▲
                                                                               │ same note format
                                                                    Web Clipper, Link Ingest
```

Miniflux keeps fetching and holding entries; the reader only ranks them, writes read state back, and passes saves to Obsidian, where clips from the Web Clipper and Link Ingest already land in the same format.

## Core features

Each feature is done when the check in the last column works on your Mac and on your phone.

| Feature | What it does | Done when |
| --- | --- | --- |
| Ranked list | Unread items as cards: title, source, type, length, first lines, and the reason for its rank | Cards load in under 2 seconds with a few hundred unread |
| Reading view | Clean article view inside the app, plus a link to the original; fetches full text for summary-only feeds | A summary-only feed shows the full article after one click |
| Archive | Removes the card and marks it read in Miniflux (with Undo). Opening an item, in the reading view or the original, also archives it; "Unarchive" in the reading view puts it back | Reading in Miniflux or the reader shows the same state in both |
| Digests | Pick groups of feeds, say in plain words what to include ("new EPs and shows in Oakland"), and how often (daily, weekly, every two weeks, monthly). A model reads everything those feeds published since the last digest and writes it up, each point linked to its source. Digests appear on a Digests page and, through a feed Miniflux subscribes to, in the list | A digest arrives on schedule with only what was asked for, and every point links to a real item |
| Archive page | Archived items (read in Miniflux), most recently archived first, with search, Save for ones not saved yet, and Unarchive | An item archived anywhere shows up there; Unarchive puts it back in the list |
| Save | Writes the Obsidian note (format below) and marks it read in Miniflux, so it leaves the list | The note appears in the TBR dashboard; saving twice does nothing |
| Save with a thought | A text box whose words go into the note's Notes section | Your text is in the note under Notes |
| Source weights | A slider per feed and per group | Moving a slider reorders the list right away |
| Groups and tabs | Your own groups of feeds (a feed can be in several), kept in Maniflux; tabs for All, Read, Watch and Listen, and a chip per group | Picking a tab or group shows only those items, and works with the time budget |
| Rules | Keyword and type rules with optional end dates, e.g. at most 10 Instagram posts a day | A rule changes the order or hides items; expired rules stop applying |
| Time budget | Pick a set that fits 10, 20 or 45 minutes, using reading time and video length | The set's total is within the chosen time |
| Shortcuts | j/k move, o open, v original, s save, S save with a note, x archive, u undo, 1–4 tabs, / search | All work without the mouse |
| Phone layout | Single column, large tap targets | Usable over Tailscale on your phone |

## Miniflux

The reader talks to Miniflux only through its REST API at `http://miniflux:8080/v1`, authenticated with an API key in the `X-Auth-Token` header. It keeps no copy of entries; it reads them live and stores only what Miniflux doesn't know.

| Call | Used for |
| --- | --- |
| `GET /v1/entries?status=unread&limit=500` | Building the list; read state always comes from here, so items read elsewhere disappear on their own |
| `GET /v1/entries?status=unread&after_entry_id=<last>&order=id&direction=asc` | Finding new entries to score, every few minutes |
| `GET /v1/entries?status=read&order=changed_at&direction=desc` | The Archive page |
| `GET /v1/entries?status=read&changed_after=…` (and `status=unread`) | Keeping the archive's search index current with only what changed |
| `GET /v1/entries/{id}` | Opening one entry |
| `GET /v1/entries/{id}/fetch-content` | Full text for summary-only feeds |
| `PUT /v1/entries` with `{"entry_ids":[…],"status":"read"}` | Archive, opening, save, undo and unarchive |
| `GET /v1/feeds` | Feed names and ids for the Feeds page |

If Miniflux is down, the reader shows a clear message instead of an empty list, and keeps its own data. When Miniflux deletes old entries (read ones after 60 days by default), the reader drops their scores.

## Obsidian

Saving in the reader is the same decision as clipping: it ends up as a note in `content/`, where the dashboards and Bases pick it up. The reader keeps no read-later list of its own.

Since November 2026 the note itself is written by the library service (`library/`), which every saving tool hands its links to; its format is in `library/README.md`. A note saved from Maniflux looks like this:

```markdown
---
title: "Why Politics Has Become So Frenzied"
url: "https://…"
source: "The Atlantic"
creators:
  - "[[people/Jane Doe|Jane Doe]]"
via: "[[orgs/The Atlantic|The Atlantic]]"
published: 2026-10-27
date-added: 2026-10-30T12:00:00-07:00
type: writing
form:
minutes: 9
description: "A 2-3 sentence plain summary of what the piece covers."
date-archived:
---

(your thought, if you saved with one)
```

- `source` is the feed it came through; `via` is the outlet that published it.
- Maniflux sends the item's text, so the library service needn't fetch the page; it writes the description, type and the rest from it.
- Saving doesn't wait for the model: the card leaves at once. If the library service can't be reached, nothing is saved and the card stays, so the save can be tried again.
- Process New Clips (a QuickAdd macro on the Mac) then links the note at the top of that day's journal entry, below the tasks, once it's ready. It does the same for notes from the Web Clipper and Link Ingest.

Notes keep the link, the description and your notes; the item text isn't stored.

Rules that prevent conflicts:

1. Nothing edits what you write in a note. The library service only changes properties, and never renames or deletes a note; that's left to Obsidian.
2. A link already in `content/` is never saved again; it's linked in that day's journal again instead.
3. Saving marks the item read in Miniflux, so it leaves the reader; from then on it lives only in Obsidian.
4. A note with broken frontmatter is skipped, never treated as an error.

## Data

The reader keeps one small SQLite file holding only what Miniflux doesn't, keyed by Miniflux's entry and feed ids.

| Table | Holds |
| --- | --- |
| `scores` | Entry id, score, reason, when scored |
| `weights` | Feed id and its weight |
| `feed_group`, `feed_group_member` | Your groups (name, weight) and which feeds are in each |
| `rules` | Rule text, kind (keyword, type, limit by type, feed or group), active, end date |
| `feedback` | Entry id, action (read, save, open, original), time, plus title, URL and feed id |
| `saved` | Entry id, URL, path of the Obsidian note |
| `digest`, `digest_group`, `digest_run` | Each digest's instructions, groups and schedule, and every digest written (text, period, the items it read) |
| `state` | Highest entry id seen, settings |

Feedback is recorded from day one even though version one barely uses it: it becomes the training signal for LLM scoring later.

## Stack and setup

Python with FastAPI, SQLite and server-rendered HTML with htmx, run as one more service in `docker-compose.yml`.

- **Address:** `http://127.0.0.1:8090` on the Mac; the phone reaches it through Tailscale.
- **Vault access:** the vault's `content/` folder is mounted into the container so the reader can check for notes already saved.
- **Settings in `.env`:** Miniflux API key, path to the vault's `content/` folder, OpenRouter key for digests (and optionally `OPENROUTER_MODEL`), and `LIBRARY_TOKEN` for the library service.
- **No frontend build step:** pages are HTML from the server, with htmx for buttons, so changes are quick to make and test.

## Later

These build on version one in roughly this order; none needs a redesign.

1. LLM scoring through OpenRouter: plain-language rules, and a reason and summary per item in the list.
2. Highlights from the reader added under `## Highlights` in the note.
3. People and artists: a "Promote to Obsidian" button that creates a `people/` note with tier and feeds; tiers for Bandcamp follows.
4. Semantic search and categories described in your own words: an embedding per entry (about 75 MB for 50,000 entries, searched in ~10 ms without a vector database), made by a small model through Ollama on the Mac; categories are a description plus a similarity threshold you adjust, combined with ordinary filters, with an optional LLM check on borderline matches.
5. Grouping the same story across sources.
6. Learning taste from saved items and notes in Obsidian.
7. Converters for Instagram and Resident Advisor.
8. Friends' picks: publishing your saves as a feed and using theirs as a signal.

## Decisions

- **Name:** Maniflux.
- **Vault:** notes go to `~/Documents/Obsidian Vault/content/`.
- **Miniflux API key:** created; it lives in the private `.env` file, never in this doc.
- **Saved notes:** the link, a description and your notes; no full article text.
- **Saving ends an item's time in Maniflux:** it's marked read in Miniflux and leaves the list. Finishing it in Obsidian (filling in `date-archived`) needs no sync back.
- **Type and description are filled by Maniflux at save time**, so notes reach Obsidian finished. Process New Clips in Obsidian only links them in the journal.
- **No undo for save.** Archive can be undone.
- **Search matches the start of words** ("RIP J" finds "RIP Juan Atkins"), in both the list and the archive. Miniflux's own search only matches whole words, so the archive is searched in Maniflux from an in-memory index kept current with Miniflux's "changed since" filter.
- **No separate hide.** Archive is the one "done with this" action (it marks the item read in Miniflux), and opening an item archives it.
- **Feedback rows keep the title, URL and feed id**, so they still mean something after Miniflux deletes the entry.
- **Video length:** Miniflux's `FETCH_YOUTUBE_WATCH_TIME` is on.
- **Groups live in Maniflux, not Miniflux categories**, because a feed can be in several groups and groups carry weights and limits. Miniflux categories are ignored; feeds can all stay in "All" there. Other reader apps won't see the groups.
