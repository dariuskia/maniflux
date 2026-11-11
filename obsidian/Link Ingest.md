QuickAdd user script: paste or share a link and create a note in `content/` with frontmatter. Process New Clips (on the Mac) links new notes in the journal; if the link is already saved, this links the existing note under today's header itself. QuickAdd runs the first js block below; this text is ignored.

```js
module.exports = {
  entry: start,
  settings: {
    name: "Link Ingest",
    author: "Darius",
    options: {
      "OpenRouter API Key": {
        type: "secret",
        id: "openrouter-api-key",
        placeholder: "sk-or-...",
        description: "OpenRouter API key. Secrets don't sync, so enter it on each device.",
      },
      "Model": {
        type: "text",
        defaultValue: "anthropic/claude-haiku-4.5",
        description: "OpenRouter model slug used for the type, authors and summary",
      },
      "Content Folder": {
        type: "text",
        defaultValue: "content",
        description: "Folder for new notes",
      },
      "People Folder": {
        type: "text",
        defaultValue: "people",
        description: "Folder that author links point into",
      },
      "Journal Path": {
        type: "text",
        defaultValue: "mononoke 🐺.md",
        description: "Journal note that gets the link",
      },
    },
  },
};

const TYPES = ["video", "article", "paper", "podcast", "music", "thread", "book", "film", "other"];
const normType = (t) => (t === "essay" || t === "blog-post" ? "article" : t);
// Forms the model may pick, per library type (see library/README.md in the feed repo)
const FORMS = {
  writing: ["essay", "news", "review", "interview", "newsletter", "thread", "guide"],
  research: ["paper", "report"],
  music: ["album", "ep", "track", "mix", "radio show", "performance"],
  video: ["interview", "talk", "video essay"],
  podcast: ["episode"],
  film: ["documentary", "short"],
};
// The kinds above decide how a link is read; the note gets the library types (see library/README.md in the feed repo)
const NOTE_TYPE = {
  article: ["writing", ""], paper: ["research", "paper"], thread: ["writing", "thread"],
  video: ["video", ""], podcast: ["podcast", "episode"], music: ["music", ""], book: ["book", ""],
  film: ["film", ""],
  other: ["project", ""],
};

async function start(params, settings) {
  const { app, quickAddApi, variables, obsidian } = params;
  const { requestUrl, Notice, TFile, moment } = obsidian;
  let status;

  try {
    const apiKey = settings["OpenRouter API Key"];
    const model = settings["Model"] || "anthropic/claude-haiku-4.5";
    const folder = (settings["Content Folder"] || "content").replace(/\/+$/, "");
  const peopleFolder = (settings["People Folder"] || "people").replace(/\/+$/, "");
  const personLink = (name) => `[[${peopleFolder}/${name}|${name}]]`;
    const journalPath = settings["Journal Path"] || "mononoke 🐺.md";
    const today = moment().format("YYYY-MM-DD");

    // 1. Ask for the link and where it was found, on one screen.
    //    If the link came from the share-sheet URI, only the source is asked.
    const answers = await quickAddApi.requestInputs([
      { id: "url", label: "Link", type: "text", placeholder: "https://..." },
      { id: "source", label: "Where did you find it?", type: "text", placeholder: "e.g. aaron sent it", optional: true },
    ]);
    const raw = (answers.url || variables.url || "").trim();
    const source = clean(answers.source || "");
    const url = cleanUrl(raw);
    if (!/^https?:\/\//i.test(url)) params.abort("That doesn't look like a link.");

    // 2. If this URL is already saved, just link the existing note
    const existing = app.vault.getMarkdownFiles().find(
      (f) => f.path.startsWith(folder + "/") &&
        app.metadataCache.getFileCache(f)?.frontmatter?.url === url
    );
    if (existing) {
      await addToJournal(app, TFile, journalPath, app.fileManager.generateMarkdownLink(existing, journalPath), today);
      new Notice(`Already saved, linked in journal: ${existing.basename}`);
      return;
    }

    status = new Notice("Reading link…", 0);

    // 3. Collect metadata and page text
    const forcedType = ruleType(url);
    let meta;
    try {
      meta = (forcedType === "video" || forcedType === "music") && isYouTube(url)
        ? await gatherYouTube(url, requestUrl)
        : await gatherPage(url, requestUrl);
    } catch (e) {
      console.warn("Link Ingest: fetch failed", e);
      meta = { title: "", siteName: "", description: "", published: "", authors: [], text: "", generator: "" };
    }
    const type = forcedType || (/substack/i.test(meta.generator) ? "article" : null);

    // 4. Ask the model for type, authors, summary (fall back to metadata if it fails)
    let ai = {};
    if (apiKey) {
      status.setMessage("Summarizing…");
      try {
        ai = await askModel({ apiKey, model, url, meta, forcedType: type, requestUrl });
      } catch (e) {
        console.error("Link Ingest: model call failed", e);
        new Notice(`Summary failed, saved metadata only: ${e.message}`, 8000);
      }
    } else {
      new Notice("No OpenRouter API key set, saving metadata only.", 8000);
    }

    const title = clean(ai.title || meta.title || new URL(url).hostname);
    let finalType = type || (TYPES.includes(normType(ai.type)) ? normType(ai.type) : "other");
    if (finalType === "video" && ai.is_music === true) finalType = "music"; // a set, mix, performance or music video
    const authors = dedupe(
      (Array.isArray(ai.authors) && ai.authors.length ? ai.authors : meta.authors.length ? meta.authors : meta.channel ? [meta.channel] : [])
        .map((a) => String(a).replace(/[\[\]|#^]/g, "").trim())
        .filter(Boolean)
    );
    const description = clean(ai.description || meta.description || "");
    const published = toDate(ai.published, moment) || toDate(meta.published, moment);

    // Length in minutes: video length if known, else word count at ~238 wpm,
    // else the model's estimate (used for papers, podcasts, books and threads,
    // where the fetched page isn't the full content)
    let minutes = meta.minutes || null;
    const pageIsFullText = !["paper", "podcast", "music", "book", "film", "thread", "video"].includes(finalType);
    if (!minutes && pageIsFullText && meta.words > 150) minutes = Math.max(1, Math.round(meta.words / 238));
    if (!minutes && Number(ai.minutes) > 0) minutes = Math.round(Number(ai.minutes));
    if (finalType === "book") minutes = null; // books don't get a length estimate

    // 5. Write the note
    const [noteType, ruleForm] = NOTE_TYPE[finalType] || ["project", ""];
    const aiForm = String(ai.form || "").toLowerCase().trim();
    const noteForm = (FORMS[noteType] || []).includes(aiForm) ? aiForm : ruleForm;
    const q = (s) => JSON.stringify(String(s));
    const fm = [
      "---",
      `title: ${q(title)}`,
      `url: ${q(url)}`,
      `source: ${source ? q(source) : ""}`,
      "creators:",
      ...authors.map((a) => `  - ${q(personLink(a))}`),
      `published: ${published}`,
      `date-added: ${moment().format()}`,
      `type: ${noteType}`,
      `form: ${noteForm}`,
      ...(meta.image ? [`cover: ${q(meta.image)}`] : []),
      `minutes: ${minutes ?? ""}`,
      `description: ${q(description)}`,
      "tags:",
      "  - clippings",
      `  - ${noteType}`,
      "date-archived:",
      "---",
      "",
    ].join("\n");

    if (!app.vault.getAbstractFileByPath(folder)) await app.vault.createFolder(folder);
    const base = safeFileName(title) || safeFileName(new URL(url).hostname);
    let path = `${folder}/${base}.md`;
    for (let n = 2; app.vault.getAbstractFileByPath(path); n++) path = `${folder}/${base} (${n}).md`;
    await app.vault.create(path, fm);
    // Process New Clips on the Mac links the new note in the journal

    status.hide();
    new Notice(`Saved ${noteType}: ${title}`);
  } catch (e) {
    status?.hide();
    if (e?.name === "MacroAbortError") throw e;
    console.error("Link Ingest:", e);
    new Notice(`Link Ingest failed: ${e.message}`, 10000);
    throw e;
  }
}

// ---------- Journal ----------

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
    if (lines.slice(header + 1, end).includes(entry)) return text;

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

// ---------- Fetching ----------

function isYouTube(url) {
  return /(^|\.)youtube\.com$|^youtu\.be$/.test(host(url));
}

function ruleType(url) {
  const h = host(url);
  let path = "";
  try { path = new URL(url).pathname; } catch (e) { /* no path */ }
  if (/^music\.youtube\.com$|^music\.apple\.com$|(^|\.)soundcloud\.com$|(^|\.)bandcamp\.com$/.test(h)) return "music";
  if (/^(nts\.live|mixcloud\.com)$/.test(h)) return "music";
  if (h === "open.spotify.com") {
    if (/^\/(intl-[^/]+\/)?(track|album|playlist|artist)\//.test(path)) return "music";
    return "podcast";
  }
  if (/(^|\.)youtube\.com$|^youtu\.be$|(^|\.)vimeo\.com$/.test(h)) return "video";
  // Films and books: the library service on the server then adds the year, cover and IDs
  if (h === "letterboxd.com" && /^\/film\//.test(path)) return "film";
  if (/(^|\.)imdb\.com$/.test(h) && /^\/title\//.test(path)) return "film";
  if (h === "goodreads.com" && /^\/(en\/)?book\/show\//.test(path)) return "book";
  if (h === "openlibrary.org" && /^\/(works|books)\//.test(path)) return "book";
  if (h === "storygraph.com" || h === "app.thestorygraph.com") { if (/^\/books\//.test(path)) return "book"; }
  if (/(^|\.)(arxiv\.org|openreview\.net|biorxiv\.org|ssrn\.com|aclanthology\.org)$/.test(h)) return "paper";
  if (/^(podcasts\.apple\.com|overcast\.fm|pca\.st)$/.test(h)) return "podcast";
  if (/^(twitter\.com|x\.com|bsky\.app)$/.test(h)) return "thread";
  if (/(^|\.)substack\.com$/.test(h)) return "article";
  return null;
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

async function gatherPage(url, requestUrl) {
  const res = await requestUrl({
    url,
    headers: {
      "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
      "Accept-Language": "en-US,en;q=0.9",
    },
    throw: false,
  });
  if (res.status >= 400) throw new Error(`HTTP ${res.status}`);
  const ctype = res.headers["content-type"] || res.headers["Content-Type"] || "";
  if (/pdf/i.test(ctype)) {
    return { title: decodeURIComponent(url.split("/").pop() || ""), siteName: host(url), description: "", published: "", authors: [], text: "", generator: "" };
  }

  const doc = new DOMParser().parseFromString(res.text, "text/html");
  const m = (sel) => doc.querySelector(sel)?.getAttribute("content")?.trim() || "";
  const ld = readJsonLd(doc);

  const metaAuthors = [m('meta[name="author"]'), m('meta[property="article:author"]'), m('meta[name="citation_author"]')]
    .filter((a) => a && !/^https?:/i.test(a));

  const meta = {
    title: m('meta[property="og:title"]') || m('meta[name="twitter:title"]') || m('meta[name="citation_title"]') || (doc.title || "").trim(),
    siteName: m('meta[property="og:site_name"]') || host(url),
    description: m('meta[property="og:description"]') || m('meta[name="description"]'),
    published: m('meta[property="article:published_time"]') || m('meta[name="citation_publication_date"]') || m('meta[name="date"]') || ld.published,
    authors: dedupe([...metaAuthors, ...ld.authors]),
    generator: m('meta[name="generator"]'),
    text: "",
  };

  doc.querySelectorAll("script, style, noscript, nav, header, footer, aside, form, svg, iframe").forEach((el) => el.remove());
  const root = doc.querySelector("article") || doc.querySelector("main") || doc.body;
  const fullText = (root?.textContent || "").replace(/\s+/g, " ").trim();
  meta.words = fullText ? fullText.split(" ").length : 0;
  meta.text = fullText.slice(0, 20000);
  return meta;
}

function readJsonLd(doc) {
  const out = { authors: [], published: "" };
  const visit = (node) => {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) return node.forEach(visit);
    if (node["@graph"]) visit(node["@graph"]);
    if (node.author) {
      [].concat(node.author).forEach((a) => {
        const n = typeof a === "string" ? a : a?.name;
        if (n && !/^https?:/i.test(n)) out.authors.push(n);
      });
    }
    if (!out.published && node.datePublished) out.published = node.datePublished;
  };
  doc.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
    try { visit(JSON.parse(s.textContent)); } catch (e) { /* ignore bad JSON-LD */ }
  });
  return out;
}

// ---------- Model (OpenRouter) ----------

async function askModel({ apiKey, model, url, meta, forcedType, requestUrl }) {
  const prompt = [
    "You are filing a link in a personal reading log. Using the page data below, reply with only a JSON object with these keys:",
    '- "title": the title of the content, without site-name suffixes',
    `- "type": one of ${TYPES.join(", ")} (essays and blog posts count as article)${forcedType ? ` (it is a ${forcedType})` : ""}`,
    '- "authors": array of the people who made it or are its subject. For music: the artists or DJs performing, not the channel, station or label that posted it. For an interview: the person interviewed, then the interviewer if named. Use an organization only if no person is involved; [] if unknown',
    '- "is_music": true if this is music to listen to: a DJ set, mix, radio show, live performance, session, music video, album or track. A video where the artist plays or performs music counts, even with talking in between. false if it is mainly about something (an interview without performance, a talk, review or documentary), even when the subject is music',
    `- "form": one of ${JSON.stringify(FORMS)} for its kind, or "" if none fits`,
    '- "description": a 2-3 sentence plain summary of what it says or covers',
    '- "published": publication date as YYYY-MM-DD, or "" if unknown',
    '- "minutes": your estimate of how many minutes it takes to read, watch or listen to the full piece (a number; for a paper, the full paper, not just the abstract; for music, the track or album)',
    "",
    `URL: ${url}`,
    `Site: ${meta.siteName}`,
    `Metadata title: ${meta.title}`,
    `Metadata authors: ${meta.authors.join(", ")}`,
    ...(meta.channel ? [`Channel that posted it: ${meta.channel}`] : []),
    ...(meta.category ? [`Category chosen by the uploader: ${meta.category}`] : []),
    `Metadata published: ${meta.published}`,
    `Metadata description: ${meta.description}`,
    "",
    "Page text:",
    meta.text || "(none available)",
  ].join("\n");

  const res = await requestUrl({
    url: "https://openrouter.ai/api/v1/chat/completions",
    method: "POST",
    contentType: "application/json",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "X-Title": "Obsidian Link Ingest",
    },
    body: JSON.stringify({ model, max_tokens: 800, messages: [{ role: "user", content: prompt }] }),
    throw: false,
  });

  if (res.status !== 200) {
    let msg = res.text.slice(0, 200);
    try { msg = res.json?.error?.message || msg; } catch (e) { /* keep raw text */ }
    throw new Error(`OpenRouter ${res.status}: ${msg}`);
  }

  const content = res.json?.choices?.[0]?.message?.content;
  const out = Array.isArray(content) ? content.map((c) => c.text || "").join("") : String(content || "");
  const match = out.match(/\{[\s\S]*\}/);
  if (!match) throw new Error("The model didn't return JSON");
  return JSON.parse(match[0]);
}

// ---------- Helpers ----------

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

function dedupe(arr) {
  const seen = new Set();
  return arr.filter((x) => {
    const k = x.toLowerCase();
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

function toDate(s, moment) {
  if (!s) return "";
  const d = moment(s, moment.ISO_8601, true).isValid() ? moment(s, moment.ISO_8601) : moment(new Date(s));
  return d.isValid() ? d.format("YYYY-MM-DD") : "";
}

function safeFileName(s) {
  return String(s || "").replace(/[\\/:*?"<>|#^\[\]]/g, "").replace(/\s+/g, " ").trim().slice(0, 120);
}
```