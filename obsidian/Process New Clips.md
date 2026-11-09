QuickAdd user script: watches `content/` for new notes (from the Web Clipper, Link Ingest, Maniflux or anything else) and adds a link to each one at the top of that day's entry in the journal, below the day's tasks. When it starts it also catches up, linking notes from the last few days that the journal doesn't link yet. Notes arrive already filled in (the Web Clipper, Link Ingest and Maniflux each write their own type and description), so this only links. Replaces Save clippings. Set the macro to "Run on plugin load". QuickAdd's settings sync to the phone too, so the script itself does nothing on phones and tablets: there it could run before Sync has brought the latest notes down and link them under stale dates. Notes dated in the future aren't linked; a notice asks you to fix their date-added. When today has no heading yet, it adds one from the date template, the same as the journal's "add date" button, so today starts with its checkboxes.

```js
module.exports = {
  entry: start,
  settings: {
    name: "Process New Clips",
    author: "Darius",
    options: {
      "Content Folder": {
        type: "text",
        defaultValue: "content",
        description: "Folder to watch",
      },
      "Journal Path": {
        type: "text",
        defaultValue: "mononoke 🐺.md",
        description: "Journal note that gets the links",
      },
      "Date Template": {
        type: "text",
        defaultValue: "templates/Date template.md",
        description: "Template for a new day in the journal, the one the journal's \"add date\" button uses",
      },
      "Days": {
        type: "text",
        defaultValue: "7",
        description: "How many days back to link notes that aren't in the journal yet, including today",
      },
    },
  },
};

async function start(params, settings) {
  const { app, obsidian } = params;
  const { TFile, moment, Platform } = obsidian;
  // Mac only. On a phone it can run before Sync brings down the latest notes and journal.
  if (Platform.isMobile) return;

  const config = {
    folder: (settings["Content Folder"] || "content").replace(/\/+$/, ""),
    journalPath: settings["Journal Path"] || "mononoke 🐺.md",
    templatePath: settings["Date Template"] || "templates/Date template.md",
    days: Math.max(1, parseInt(settings["Days"], 10) || 7),
  };

  // One shared state per app session, so running the macro again doesn't add a second watcher
  const state = (window.__processNewClips ??= {
    watching: false,
    timers: new Map(),
    journalQueue: Promise.resolve(),
  });
  state.config = config; // latest settings win

  const inFolder = (f) => f instanceof TFile && f.extension === "md" && f.path.startsWith(state.config.folder + "/");

  const schedule = (file) => {
    if (!inFolder(file)) return;
    clearTimeout(state.timers.get(file.path));
    // Short wait so a note that is still being written settles first
    state.timers.set(file.path, setTimeout(() => {
      state.timers.delete(file.path);
      linkInJournal(app, obsidian, state, file);
    }, 1500));
  };

  if (!state.watching) {
    state.watching = true;
    // Registered after startup, so the files Obsidian loads on launch don't count as added
    app.workspace.onLayoutReady(() => {
      // A new note created in the folder (clipped, saved by Maniflux, synced in, or made by hand)
      app.vault.on("create", (file) => schedule(file));
      // A note moved into the folder from somewhere else
      app.vault.on("rename", (file, oldPath) => {
        if (!oldPath.startsWith(state.config.folder + "/")) schedule(file);
      });
    });
  }

  // Catch up on anything added while Obsidian was closed, once Obsidian knows every link in the vault
  app.workspace.onLayoutReady(async () => {
    await waitForLinks(app);
    const journal = app.vault.getAbstractFileByPath(state.config.journalPath);
    const linked = (journal && app.metadataCache.resolvedLinks[journal.path]) || {};
    for (const f of app.vault.getMarkdownFiles()) {
      if (!inFolder(f) || linked[f.path]) continue;
      const fm = app.metadataCache.getFileCache(f)?.frontmatter;
      if (isRecent(addedAt(f, fm, moment), state.config.days, moment)) schedule(f);
    }
  });
}

// When the note was added: its date-added, else when the file was created
function addedAt(file, fm, moment) {
  const added = fm?.["date-added"];
  return added && moment(String(added)).isValid() ? moment(String(added)) : moment(file.stat.ctime);
}

// Within the last few days, and not in the future (a future date-added is a typo)
function isRecent(when, days, moment) {
  return !when.isBefore(moment().startOf("day").subtract(days - 1, "days")) && !isFuture(when, moment);
}

function isFuture(when, moment) {
  return when.isAfter(moment().endOf("day"));
}

// Resolves once Obsidian has worked out the links between notes (or after 10 seconds)
function waitForLinks(app, ms = 10000) {
  return new Promise((resolve) => {
    const done = () => {
      app.metadataCache.offref(ref);
      clearTimeout(timer);
      resolve();
    };
    const ref = app.metadataCache.on("resolved", done);
    const timer = setTimeout(done, ms);
  });
}

// Resolves once Obsidian has read the note's frontmatter (or after 10 seconds)
function waitForCache(app, file, ms = 10000) {
  if (app.metadataCache.getFileCache(file)?.frontmatter) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      app.metadataCache.offref(ref);
      clearTimeout(timer);
      resolve();
    };
    const ref = app.metadataCache.on("changed", (f) => { if (f.path === file.path) done(); });
    const timer = setTimeout(done, ms);
  });
}

function linkInJournal(app, obsidian, state, file) {
  // One journal edit at a time, so links added together don't trip over each other
  const run = state.journalQueue.then(() => addLink(app, obsidian, state, file));
  state.journalQueue = run.catch(() => {});
  return run.catch((e) => {
    console.error("Process New Clips: journal link failed", file.path, e);
    new obsidian.Notice(`Couldn't link ${file.basename} in the journal: ${e.message}`, 10000);
  });
}

async function addLink(app, obsidian, state, file) {
  const { TFile, moment } = obsidian;
  if (!app.vault.getAbstractFileByPath(file.path)) return; // deleted meanwhile
  // date-added decides the day, so wait until Obsidian has read the frontmatter
  await waitForCache(app, file);
  const fm = app.metadataCache.getFileCache(file)?.frontmatter;
  const when = addedAt(file, fm, moment);
  if (isFuture(when, moment)) {
    new obsidian.Notice(`Not linking ${file.basename}: its date-added (${when.format("YYYY-MM-DD")}) is in the future.`, 10000);
    return;
  }
  // Old notes moved into the folder aren't news
  if (!isRecent(when, state.config.days, moment)) return;

  const journal = app.vault.getAbstractFileByPath(state.config.journalPath);
  if (!(journal instanceof TFile)) throw new Error(`Journal not found at "${state.config.journalPath}"`);
  // Already linked anywhere in the journal (e.g. Link Ingest linking an existing note)
  if (app.metadataCache.resolvedLinks[journal.path]?.[file.path]) return;

  const link = app.fileManager.generateMarkdownLink(file, journal.path);
  const day = when.format("YYYY-MM-DD");
  // A new heading for today comes from the date template, like the journal's "add date" button
  const dayLines = day === moment().format("YYYY-MM-DD") ? await dateTemplate(app, obsidian, state, journal) : null;
  await app.vault.process(journal, (text) => insertLink(text.split("\n"), link, day, dayLines).join("\n"));
}

// The date template filled in the way Obsidian's Templates plugin does it ({{date}}, {{time}}, {{title}}),
// as lines without the blank ones around it. Null if the template is missing.
async function dateTemplate(app, obsidian, state, journal) {
  const template = app.vault.getAbstractFileByPath(state.config.templatePath);
  if (!(template instanceof obsidian.TFile)) return null;
  const options = app.internalPlugins?.plugins?.templates?.instance?.options || {};
  const now = obsidian.moment();
  const text = (await app.vault.read(template))
    .replace(/{{\s*date\s*(?::(.*?))?}}/gi, (_, fmt) => now.format(fmt || options.dateFormat || "YYYY-MM-DD"))
    .replace(/{{\s*time\s*(?::(.*?))?}}/gi, (_, fmt) => now.format(fmt || options.timeFormat || "HH:mm"))
    .replace(/{{\s*title\s*}}/gi, journal.basename);
  const lines = text.split("\n");
  while (lines.length && lines[0].trim() === "") lines.shift();
  while (lines.length && lines[lines.length - 1].trim() === "") lines.pop();
  return lines.length ? lines : null;
}

// Insert a link under the given day's header, after that day's last task.
// Same placement as Link Ingest; missing headers go in date order (newest first).
// dayLines, if given, is the filled-in date template to use for a missing header.
function insertLink(lines, link, day, dayLines = null) {
  const entry = `${link}`;
  const isDateHeader = (l) => /^##\s+\d{4}-\d{2}-\d{2}/.test(l);
  const dateOf = (l) => l.match(/^##\s+(\d{4}-\d{2}-\d{2})/)[1];

  let header = lines.findIndex((l) => l.trim().startsWith(`## ${day}`));

  // No header for today: add the date template where the "add date" button would (above the newest
  // day), then place the link under it as usual
  if (header === -1 && dayLines && dayLines.some((l) => l.trim().startsWith(`## ${day}`))) {
    let at = lines.findIndex((l) => isDateHeader(l) && dateOf(l) < day);
    if (at === -1) {
      while (lines.length && lines[lines.length - 1].trim() === "") lines.pop();
      lines.push("");
      at = lines.length;
    }
    lines.splice(at, 0, ...dayLines, "");
    header = lines.findIndex((l) => l.trim().startsWith(`## ${day}`));
  }

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
