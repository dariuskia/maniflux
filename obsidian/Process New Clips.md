QuickAdd user script: watches `content/` for new notes (from the Web Clipper, Link Ingest, Maniflux or anything else) and adds a link to each one at the top of that day's entry in the journal, below the day's tasks. When it starts it also catches up, linking notes from the last few days that the journal doesn't link yet. Notes are filled in by the library service on the personal server (see library/README.md in the feed repo). A note it's still working on says status: pending and isn't linked yet; when it says status: ready, this links it and removes the status. A save of something already in the library either sets relink on the existing note (linked again under that day) or arrives as status: duplicate, which is merged into the original: anything written in it is added to the original, the original is linked again, and the duplicate goes to the trash. Replaces Save clippings. Set the macro to "Run on plugin load". QuickAdd's settings sync to the phone too, so the script itself does nothing on phones and tablets: there it could run before Sync has brought the latest notes down and link them under stale dates. Notes dated in the future aren't linked; a notice asks you to fix their date-added. When today has no heading yet, it adds one from the date template, the same as the journal's "add date" button, so today starts with its checkboxes.

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
      // A note the library service has finished (ready), found to be a duplicate, or asked to link
      // again. Obsidian has already read the note's properties, so this check costs next to nothing.
      app.metadataCache.on("changed", (file, _data, cache) => {
        if (inFolder(file) && needsAction(cache?.frontmatter)) schedule(file);
      });
    });
  }

  // Catch up on anything added while Obsidian was closed, once Obsidian knows every link in the vault
  app.workspace.onLayoutReady(async () => {
    await waitForLinks(app);
    const journal = app.vault.getAbstractFileByPath(state.config.journalPath);
    const linked = (journal && app.metadataCache.resolvedLinks[journal.path]) || {};
    for (const f of app.vault.getMarkdownFiles()) {
      if (!inFolder(f)) continue;
      const fm = app.metadataCache.getFileCache(f)?.frontmatter;
      if (needsAction(fm)) { schedule(f); continue; }
      if (linked[f.path]) continue;
      if (isRecent(addedAt(f, fm, moment), state.config.days, moment)) schedule(f);
    }
  });
}

// Set by the library service: finished (ready), a duplicate to merge, or "link this again"
function needsAction(fm) {
  return !!fm && (fm.status === "ready" || fm.status === "duplicate" || !!fm.relink);
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
  // The library service is still working on it: linked once it's ready
  if (fm?.status === "pending") return;
  if (fm?.status === "duplicate") return mergeDuplicate(app, obsidian, state, file, fm);
  if (fm?.relink) {
    await linkUnder(app, obsidian, state, file, String(fm.relink).slice(0, 10), true);
    await clearProps(app, file, ["relink"]);
    return;
  }
  // Saved offline, so named after the site: take the real title the library service found. Renamed
  // through Obsidian, so the note stays open and every link to it (the journal included) follows.
  if (fm?.["rename-to"]) await renameTo(app, file, String(fm["rename-to"]));
  const when = addedAt(file, fm, moment);
  if (isFuture(when, moment)) {
    new obsidian.Notice(`Not linking ${file.basename}: its date-added (${when.format("YYYY-MM-DD")}) is in the future.`, 10000);
    return;
  }
  // Old notes moved into the folder aren't news
  if (isRecent(when, state.config.days, moment)) await linkUnder(app, obsidian, state, file, when.format("YYYY-MM-DD"), false);
  if (fm?.status === "ready") await clearProps(app, file, ["status"]);
}

// Links the note under that day's heading. force: even if the journal already links it elsewhere.
async function linkUnder(app, obsidian, state, file, day, force) {
  const { TFile, moment } = obsidian;
  const journal = app.vault.getAbstractFileByPath(state.config.journalPath);
  if (!(journal instanceof TFile)) throw new Error(`Journal not found at "${state.config.journalPath}"`);
  // Already linked anywhere in the journal (e.g. Link Ingest linking an existing note)
  if (!force && app.metadataCache.resolvedLinks[journal.path]?.[file.path]) return;

  const link = app.fileManager.generateMarkdownLink(file, journal.path);
  // A new heading for today comes from the date template, like the journal's "add date" button
  const dayLines = day === moment().format("YYYY-MM-DD") ? await dateTemplate(app, obsidian, state, journal) : null;
  await app.vault.process(journal, (text) => insertLink(text.split("\n"), link, day, dayLines, force).join("\n"));
}

async function renameTo(app, file, name) {
  const safe = name.replace(/[\\/:*?"<>|#^\[\]]/g, "").replace(/\s+/g, " ").trim().slice(0, 120);
  const folder = file.parent?.path || "";
  if (safe && safe !== file.basename) {
    let path = `${folder}/${safe}.md`;
    for (let n = 2; app.vault.getAbstractFileByPath(path); n++) path = `${folder}/${safe} (${n}).md`;
    await app.fileManager.renameFile(file, path); // updates links everywhere; file now has the new path
  }
  await clearProps(app, file, ["rename-to"]);
}

async function clearProps(app, file, keys) {
  if (!app.vault.getAbstractFileByPath(file.path)) return;
  await app.fileManager.processFrontMatter(file, (fm) => { for (const k of keys) delete fm[k]; });
}

// A second save of something already in the library: add anything written in it (and where it was
// found) to the original, link the original again under the day of the second save, and trash it.
async function mergeDuplicate(app, obsidian, state, dup, fm) {
  const { moment, Notice } = obsidian;
  const target = String(fm["duplicate-of"] || "").replace(/^\[\[|\]\]$/g, "").split("|")[0];
  const original = target && app.metadataCache.getFirstLinkpathDest(target, dup.path);
  if (!original || original.path === dup.path) {
    new Notice(`${dup.basename} is marked as a duplicate of "${target}", which wasn't found. Left as it is.`, 10000);
    return;
  }
  const text = await app.vault.read(dup);
  const body = text.replace(/^---\n[\s\S]*?\n---\n?/, "").replace(/^\s*## Notes\s*/, "").trim();
  const origSource = String(app.metadataCache.getFileCache(original)?.frontmatter?.source || "");
  const extra = [];
  if (fm.source && String(fm.source).trim() && !origSource.includes(String(fm.source).trim())) extra.push(`Also found via: ${fm.source}`);
  if (body) extra.push(body);
  if (extra.length) await app.vault.process(original, (t) => t.trimEnd() + "\n\n" + extra.join("\n\n") + "\n");
  await linkUnder(app, obsidian, state, original, addedAt(dup, fm, moment).format("YYYY-MM-DD"), true);
  await app.fileManager.trashFile(dup);
  new Notice(`Already saved: ${original.basename}. Linked it again${extra.length ? " and added what you wrote" : ""}.`, 8000);
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
// again: the note was saved again, so its link goes at the top of the day's links even if the day
// already links it further down (but not twice in that top group)
function insertLink(lines, link, day, dayLines = null, again = false) {
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
  // Position right after the last checkbox line
  let at = header + 1;
  for (let i = header + 1; i < end; i++) {
    if (/^\s*[-*] \[.\]/.test(lines[i])) at = i + 1;
  }
  // The links at the top of the day (right after the tasks): a link already there isn't added again
  let first = at;
  while (first < end && lines[first].trim() === "") first++;
  let topLinks = [];
  for (let i = first; i < end && /^\[\[|^\[[^\]]*\]\(/.test(lines[i]); i++) topLinks.push(lines[i]);
  if (topLinks.includes(entry)) return lines;
  if (!again && lines.slice(header + 1, end).includes(entry)) return lines;

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
