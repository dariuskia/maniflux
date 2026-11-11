QuickAdd user script: paste or share a link, say where you found it, and the library service on the personal server turns it into a finished note in `content/` (type, creators, description, cover, IDs; duplicates caught), in the background. Saving doesn't wait for that: the note appears, and Process New Clips links it in the journal, once it's ready. If the link is already saved, this links the existing note under today's header at once. If the server can't be reached (offline, or Tailscale off), it leaves a pending note that the server fills in later. YouTube's page is read here, on the device, because YouTube won't show it to the server. QuickAdd runs the first js block below; this text is ignored.

```js
module.exports = {
  entry: start,
  settings: {
    name: "Link Ingest",
    author: "Darius",
    options: {
      "Library URL": {
        type: "text",
        defaultValue: "",
        placeholder: "https://home.<tailnet>.ts.net:8444",
        description: "The library service, reached over Tailscale",
      },
      "Library Token": {
        type: "secret",
        id: "library-token",
        placeholder: "the LIBRARY_TOKEN from the server's .env",
        description: "Secrets don't sync, so enter it on each device.",
      },
      "Content Folder": {
        type: "text",
        defaultValue: "content",
        description: "Folder for new notes",
      },
      "Journal Path": {
        type: "text",
        defaultValue: "mononoke 🐺.md",
        description: "Journal note that gets the link when it's already saved",
      },
    },
  },
};

async function start(params, settings) {
  const { app, quickAddApi, variables, obsidian } = params;
  const { requestUrl, Notice, TFile, moment } = obsidian;
  const folder = (settings["Content Folder"] || "content").replace(/\/+$/, "");
  const journalPath = settings["Journal Path"] || "mononoke 🐺.md";
  const today = moment().format("YYYY-MM-DD");
  const linkExisting = async (file) => {
    await addToJournal(app, TFile, journalPath, app.fileManager.generateMarkdownLink(file, journalPath), today);
    new Notice(`Already saved, linked in journal: ${file.basename}`);
  };

  // 1. The link and where it was found, on one screen (only the source if the link came from the share sheet)
  const answers = await quickAddApi.requestInputs([
    { id: "url", label: "Link", type: "text", placeholder: "https://..." },
    { id: "source", label: "Where did you find it?", type: "text", placeholder: "e.g. aaron sent it", optional: true },
  ]);
  const url = cleanUrl((answers.url || variables.url || "").trim());
  const source = clean(answers.source || "");
  if (!/^https?:\/\//i.test(url)) params.abort("That doesn't look like a link.");

  // 2. Already saved: link the existing note, at once
  const existing = app.vault.getMarkdownFiles().find(
    (f) => f.path.startsWith(folder + "/") && app.metadataCache.getFileCache(f)?.frontmatter?.url === url
  );
  if (existing) return linkExisting(existing);

  // 3. YouTube's details, read here (the server can't): title, channel, category, length, description
  const job = { url, source, via: "link ingest", date_added: moment().format() };
  if (isYouTube(url)) {
    const meta = await gatherYouTube(url, requestUrl);
    Object.assign(job, {
      title: meta.title || "", channel: meta.channel || "", category: meta.category || "",
      minutes: meta.minutes || null, published: meta.published || "", description: (meta.text || "").slice(0, 5000),
    });
  }

  // 4. Hand it to the library service; it answers at once
  const base = (settings["Library URL"] || "").replace(/\/+$/, "");
  const token = settings["Library Token"] || "";
  if (base && token) {
    try {
      const res = await requestUrl({
        url: `${base}/save`, method: "POST", contentType: "application/json",
        headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify(job), throw: false,
      });
      if (res.status === 200) {
        if (res.json?.status === "exists") {
          const file = app.metadataCache.getFirstLinkpathDest(String(res.json.note || ""), "");
          if (file) return linkExisting(file);
          new Notice(`Already saved as ${res.json.note}`);
          return;
        }
        new Notice("Saved. It'll appear in the journal once it's filled in.");
        return;
      }
      console.warn("Link Ingest: library service answered", res.status, res.text?.slice(0, 200));
    } catch (e) {
      console.warn("Link Ingest: library service unreachable", e);
    }
  }

  // 5. The server is out of reach: leave a pending note for it to fill in later
  const q = (s) => JSON.stringify(String(s));
  const title = clean(job.title || host(url) || url);
  const fm = [
    "---",
    `title: ${q(title)}`,
    `url: ${q(url)}`,
    `source: ${source ? q(source) : ""}`,
    "via: link ingest",
    `date-added: ${job.date_added}`,
    ...(job.minutes ? [`minutes: ${job.minutes}`] : []),
    ...(job.channel ? [`yt-channel: ${q(job.channel)}`] : []),
    ...(job.category ? [`yt-category: ${q(job.category)}`] : []),
    "status: pending",
    "---",
    "",
  ].join("\n");
  if (!app.vault.getAbstractFileByPath(folder)) await app.vault.createFolder(folder);
  const base2 = safeFileName(title) || "Untitled";
  let path = `${folder}/${base2}.md`;
  for (let n = 2; app.vault.getAbstractFileByPath(path); n++) path = `${folder}/${base2} (${n}).md`;
  await app.vault.create(path, fm);
  new Notice(base && token
    ? "Saved for later: the server couldn't be reached, so it'll be filled in once it can."
    : "Saved for later: set the Library URL and Token in Link Ingest's settings so notes get filled in.", 8000);
}

async function addToJournal(app, TFile, journalPath, link, today) {
  const file = app.vault.getAbstractFileByPath(journalPath);
  if (!(file instanceof TFile)) throw new Error(`Journal not found at "${journalPath}"`);
  const entry = `${link}`;

  await app.vault.process(file, (text) => {
    const lines = text.split("\n");
    const header = lines.findIndex((l) => l.trim().startsWith(`## ${today}`));

    // No header for today yet: add one above the most recent date header
    // (i.e. below the tasks/button blocks). If there are no dates yet, add it at the end.
    if (header === -1) {
      const firstDate = lines.findIndex((l) => /^##\s+\d{4}-\d{2}-\d{2}/.test(l));
      if (firstDate === -1) return text.replace(/\s*$/, "") + `\n\n## ${today}\n${entry}\n`;
      lines.splice(firstDate, 0, `## ${today}`, entry, "");
      return lines.join("\n");
    }

    // Today's section runs until the next # or ## heading
    let end = lines.length;
    for (let i = header + 1; i < lines.length; i++) {
      if (/^#{1,2}\s/.test(lines[i])) { end = i; break; }
    }
    // Position right after the last checkbox line
    let at = header + 1;
    for (let i = header + 1; i < end; i++) {
      if (/^\s*[-*] \[.\]/.test(lines[i])) at = i + 1;
    }
    // Saved again: it goes at the top of the day's links, unless it's already in that top group
    let first = at;
    while (first < end && lines[first].trim() === "") first++;
    for (let i = first; i < end && /^\[\[|^\[[^\]]*\]\(/.test(lines[i]); i++) if (lines[i] === entry) return text;

    if (at > header + 1 && lines[at]?.trim() === "") {
      // Blank line already there: put the link right after it
      at = at + 1;
      lines.splice(at, 0, entry);
    } else if (at > header + 1) {
      // Right after a task: add a blank line, then the link
      lines.splice(at, 0, "", entry);
      at = at + 1;
    } else {
      // No tasks today: put the link right under the header
      lines.splice(at, 0, entry);
    }

    // Keep a blank line between the links and whatever follows (journal text or the next date)
    const next = lines[at + 1];
    if (next !== undefined && next.trim() !== "" && !/^\[\[|^\[[^\]]*\]\(/.test(next)) {
      lines.splice(at + 1, 0, "");
    }
    return lines.join("\n");
  });
}

function isYouTube(url) {
  return /(^|\.)youtube\.com$|^youtu\.be$/.test(host(url));
}

async function gatherYouTube(url, requestUrl) {
  const meta = { title: "", siteName: "YouTube", description: "", published: "", authors: [], text: "", generator: "" };
  try {
    const o = await requestUrl({ url: `https://www.youtube.com/oembed?format=json&url=${encodeURIComponent(url)}` });
    meta.title = o.json.title || "";
    // The channel is often a station, label or magazine, not who made it: the model decides
    if (o.json.author_name) meta.channel = o.json.author_name;
    if (o.json.thumbnail_url) meta.image = o.json.thumbnail_url;
  } catch (e) { console.warn("Link Ingest: oEmbed failed", e); }
  try {
    const html = (await requestUrl({ url, headers: { "Accept-Language": "en-US,en;q=0.9" } })).text;
    const desc = html.match(/"shortDescription":"((?:[^"\\]|\\.)*)"/);
    if (desc) meta.text = JSON.parse(`"${desc[1]}"`);
    const pub = html.match(/"(?:publishDate|uploadDate)":"([^"]+)"/);
    if (pub) meta.published = pub[1];
    // The category the uploader chose ("Music", "Education", ...): a strong hint, not proof
    const cat = html.match(/"category":"([^"]+)"/);
    if (cat) meta.category = cat[1];
    const len = html.match(/"lengthSeconds":"(\d+)"/);
    if (len) meta.minutes = Math.max(1, Math.round(Number(len[1]) / 60));
  } catch (e) { console.warn("Link Ingest: YouTube page fetch failed", e); }
  return meta;
}

function cleanUrl(raw) {
  const found = raw.match(/https?:\/\/\S+/i);
  if (!found) return raw;
  try {
    const u = new URL(found[0]);
    [...u.searchParams.keys()].forEach((k) => {
      if (/^utm_/i.test(k) || ["si", "fbclid", "gclid", "ref_src"].includes(k)) u.searchParams.delete(k);
    });
    return u.toString();
  } catch (e) {
    return found[0];
  }
}

function host(url) {
  try { return new URL(url).hostname.replace(/^(www|m)\./, ""); } catch (e) { return ""; }
}

function clean(s) {
  return String(s || "").replace(/\s+/g, " ").trim();
}

function safeFileName(s) {
  return String(s || "").replace(/[\\/:*?"<>|#^\[\]]/g, "").replace(/\s+/g, " ").trim().slice(0, 120);
}
```
