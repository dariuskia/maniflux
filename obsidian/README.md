# Obsidian scripts (reference copies)

Copies of the QuickAdd scripts and Datacore dashboard that live in the Obsidian vault. Maniflux writes notes in the format these expect, so keep them in sync when either side changes. The live versions are in the vault.

| File | What it does |
|---|---|
| `Link Ingest.md` | QuickAdd macro: paste or share a link, creates a note in `content/` (if the link is already saved, links the existing note in the journal) |
| `Process New Clips.md` | QuickAdd macro (run on plugin load, Mac only): watches `content/` and links each new note at the top of that day's journal entry (below the tasks). Catches up on startup. Doesn't fill any fields |
| `Link Clips to Journal.md` | Old: replaced by Process New Clips. Linked notes added to `content/` in the last 7 days under their date in the journal |
| `Reading Backfill.md` | QuickAdd macro: one-time fill of missing fields in existing `content/` notes |
| `Reading Dashboard.md` | Datacore view of `content/`: queue, by type, by length, getting old, random, recently finished |
