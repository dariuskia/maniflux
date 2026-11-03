QuickAdd user script: finds notes in `content/` added in the last 7 days that aren't linked in the journal yet, and links each one under its own date's header (below that day's tasks). Creates a missing date header in the right place. Safe to run repeatedly.

```js
module.exports = {
  entry: start,
  settings: {
    name: "Link Clips to Journal",
    author: "Darius",
    options: {
      "Content Folder": {
        type: "text",
        defaultValue: "content",
        description: "Folder with media notes",
      },
      "Journal Path": {
        type: "text",
        defaultValue: "mononoke 🐺.md",
        description: "Journal note that gets the links",
      },
      "Days": {
        type: "text",
        defaultValue: "7",
        description: "How many days back to look, including today",
      },
    },
  },
};

async function start(params, settings) {
  const { app, obsidian } = params;
  const { Notice, TFile, moment } = obsidian;

  const folder = (settings["Content Folder"] || "content").replace(/\/+$/, "");
  const journalPath = settings["Journal Path"] || "mononoke 🐺.md";
  const days = Math.max(1, parseInt(settings["Days"], 10) || 7);
  const cutoff = moment().startOf("day").subtract(days - 1, "days");

  const journal = app.vault.getAbstractFileByPath(journalPath);
  if (!(journal instanceof TFile)) params.abort(`Journal not found at "${journalPath}"`);

  // Notes the journal already links to, anywhere in the file
  const alreadyLinked = app.metadataCache.resolvedLinks[journal.path] || {};

  // Recent notes in the content folder that aren't linked yet, oldest first
  const clips = app.vault
    .getMarkdownFiles()
    .filter((f) => f.path.startsWith(folder + "/") && !alreadyLinked[f.path])
    .map((f) => {
      const added = app.metadataCache.getFileCache(f)?.frontmatter?.["date-added"];
      const when = added && moment(String(added)).isValid() ? moment(String(added)) : moment(f.stat.ctime);
      return { file: f, when };
    })
    .filter(({ when }) => !when.isBefore(cutoff))
    .sort((a, b) => a.when.valueOf() - b.when.valueOf());

  if (!clips.length) {
    new Notice(`No unlinked clips from the last ${days} days.`);
    return;
  }

  let added = 0;
  await app.vault.process(journal, (text) => {
    let lines = text.split("\n");
    for (const { file, when } of clips) {
      const link = app.fileManager.generateMarkdownLink(file, journal.path);
      const before = lines.length;
      lines = insertLink(lines, link, when.format("YYYY-MM-DD"));
      if (lines.length !== before) added++;
    }
    return lines.join("\n");
  });

  new Notice(`Linked ${added} clip${added === 1 ? "" : "s"} in the journal.`);
}

// Insert a link under the given day's header, after that day's last task.
// Same placement as Link Ingest; missing headers go in date order (newest first).
function insertLink(lines, link, day) {
  const entry = `${link}`;
  const isDateHeader = (l) => /^##\s+\d{4}-\d{2}-\d{2}/.test(l);
  const dateOf = (l) => l.match(/^##\s+(\d{4}-\d{2}-\d{2})/)[1];

  let header = lines.findIndex((l) => l.trim().startsWith(`## ${day}`));

  // No header for this day: put it above the first older date (or at the end)
  if (header === -1) {
    const older = lines.findIndex((l) => isDateHeader(l) && dateOf(l) < day);
    if (older === -1) {
      while (lines.length && lines[lines.length - 1].trim() === "") lines.pop();
      lines.push("", `## ${day}`, entry, "");
    } else {
      lines.splice(older, 0, `## ${day}`, entry, "");
    }
    return lines;
  }

  // The day's section runs until the next # or ## heading
  let end = lines.length;
  for (let i = header + 1; i < lines.length; i++) {
    if (/^#{1,2}\s/.test(lines[i])) { end = i; break; }
  }
  if (lines.slice(header + 1, end).includes(entry)) return lines;

  // Position right after the last checkbox line
  let at = header + 1;
  for (let i = header + 1; i < end; i++) {
    if (/^\s*[-*] \[.\]/.test(lines[i])) at = i + 1;
  }

  if (at > header + 1 && lines[at]?.trim() === "") {
    // Blank line already there: put the link right after it
    at = at + 1;
    lines.splice(at, 0, entry);
  } else if (at > header + 1) {
    // Right after a task: add a blank line, then the link
    lines.splice(at, 0, "", entry);
    at = at + 1;
  } else {
    // No tasks that day: put the link right under the header
    lines.splice(at, 0, entry);
  }

  // Keep a blank line between the links and whatever follows (journal text or the next date)
  const next = lines[at + 1];
  if (next !== undefined && next.trim() !== "" && !/^\[\[|^\[[^\]]*\]\(/.test(next)) {
    lines.splice(at + 1, 0, "");
  }
  return lines;
}
```
