QuickAdd user script: one-time pass over every note in `content/` that fills in fields the Link Ingest script now adds (type, minutes, description, source, date-added, tags), changes essay and blog-post types to article, clears minutes on books, and points author links into the people folder. Values that already exist are never changed. Run it once from a Macro choice.

```js
module.exports = {
  entry: start,
  settings: {
    name: "Reading Backfill",
    author: "Darius",
    options: {
      "OpenRouter API Key": {
        type: "secret",
        id: "openrouter-api-key",
        placeholder: "sk-or-...",
        description: "Same stored key as Link Ingest",
      },
      "Model": {
        type: "text",
        defaultValue: "anthropic/claude-haiku-4.5",
        description: "OpenRouter model slug used for type, length and summary",
      },
      "Content Folder": {
        type: "text",
        defaultValue: "content",
        description: "Folder with media notes",
      },
      "People Folder": {
        type: "text",
        defaultValue: "people",
        description: "Folder that author links point into",
      },
    },
  },
};

const TYPES = ["video", "article", "paper", "podcast", "music", "thread", "book", "other"];
const normType = (t) => (t === "essay" || t === "blog-post" ? "article" : t);

const blank = (v) =>
  v === undefined || v === null ||
  (typeof v === "string" && v.trim() === "") ||
  (Array.isArray(v) && v.length === 0);

// The Obsidian Web Clipper saves the page URL under `source`
const urlOf = (fm) =>
  !blank(fm.url) ? String(fm.url)
  : typeof fm.source === "string" && /^https?:\/\//i.test(fm.source.trim()) ? fm.source.trim()
  : "";

const emptyMeta = () => ({ title: "", siteName: "", description: "", published: "", authors: [], text: "", generator: "" });

async function start(params, settings) {
  const { app, quickAddApi, obsidian } = params;
  const { requestUrl, Notice, moment } = obsidian;

  const apiKey = settings["OpenRouter API Key"];
  const model = settings["Model"] || "anthropic/claude-haiku-4.5";
  const folder = (settings["Content Folder"] || "content").replace(/\/+$/, "");
  const peopleFolder = (settings["People Folder"] || "people").replace(/\/+$/, "");
  const personLink = (name) => `[[${peopleFolder}/${name}|${name}]]`;

  const notes = app.vault
    .getMarkdownFiles()
    .filter((f) => f.path.startsWith(folder + "/"))
    .map((f) => ({ file: f, fm: app.metadataCache.getFileCache(f)?.frontmatter || {} }));
  if (!notes.length) params.abort(`No notes found in ${folder}/`);

  const needsLookup = ({ fm }) =>
    !!urlOf(fm) && (blank(fm.type) || (blank(fm.minutes) && fm.type !== "book") || blank(fm.description));
  const lookups = notes.filter(needsLookup).length;

  const ok = await quickAddApi.yesNoPrompt(
    "Update reading notes?",
    `${notes.length} notes in ${folder}/. ${lookups} need the page fetched` +
      (apiKey ? " and summarized by the model" : " (no OpenRouter API key set, so no summaries)") +
      " to fill in type, length or description. Existing values won't be changed."
  );
  if (!ok) return;

  // Tapping the progress notice stops the run after the current note
  const status = new Notice("Starting…", 0);
  let stopped = false;
  status.noticeEl?.addEventListener("click", () => { stopped = true; });
  const problems = [];
  let updated = 0;

  for (let i = 0; i < notes.length; i++) {
    if (stopped) break;
    const { file, fm: cached } = notes[i];
    status.setMessage(`Updating ${i + 1}/${notes.length}: ${file.basename}\n(tap to stop)`);

    try {
      const url = urlOf(cached);
      let meta = null;
      let ai = {};
      let type = null;
      let minutes = null;

      if (needsLookup(notes[i])) {
        const forced = ruleType(url);
        try {
          meta = (forced === "video" || forced === "music") && isYouTube(url)
            ? await gatherYouTube(url, requestUrl)
            : await gatherPage(url, requestUrl);
        } catch (e) {
          meta = emptyMeta();
          problems.push(`${file.basename}: couldn't fetch page (${e.message})`);
        }

        type = forced || (/substack/i.test(meta.generator) ? "article" : null) ||
          (TYPES.includes(cached.type) ? cached.type : ["essay", "blog-post"].includes(cached.type) ? "article" : null);

        if (apiKey) {
          try {
            ai = await askModel({ apiKey, model, url, meta, forcedType: type, requestUrl });
          } catch (e) {
            problems.push(`${file.basename}: model call failed (${e.message})`);
          }
        }
        type = type || (TYPES.includes(normType(ai.type)) ? normType(ai.type) : null);

        minutes = meta.minutes || null;
        const pageIsFullText = !["paper", "podcast", "music", "book", "thread", "video"].includes(type);
        if (!minutes && pageIsFullText && meta.words > 150) minutes = Math.max(1, Math.round(meta.words / 238));
        if (!minutes && Number(ai.minutes) > 0) minutes = Math.round(Number(ai.minutes));
        if (type === "book") minutes = null; // books don't get a length estimate
      }

      const authors = dedupe(
        (Array.isArray(ai.authors) && ai.authors.length ? ai.authors : meta?.authors || [])
          .map((a) => String(a).replace(/[\[\]|#^]/g, "").trim())
          .filter(Boolean)
      );
      const description = clean(ai.description || meta?.description || "");
      const published = toDate(ai.published, moment) || toDate(meta?.published, moment);

      await app.fileManager.processFrontMatter(file, (fm) => {
        // Move a URL saved under `source` into `url`
        if (blank(fm.url) && typeof fm.source === "string" && /^https?:\/\//i.test(fm.source.trim())) {
          fm.url = fm.source.trim();
          fm.source = null;
        }

        if (blank(fm.title)) fm.title = clean(ai.title || meta?.title || file.basename);
        if (!("url" in fm)) fm.url = null;
        if (!("source" in fm)) fm.source = null;

        if (blank(fm.author) && authors.length) fm.author = authors.map(personLink);
        else if (!("author" in fm)) fm.author = null;
        else if (!blank(fm.author)) {
          // Point existing author links into the people folder: [[Name]] -> [[people/Name|Name]]
          fm.author = (Array.isArray(fm.author) ? fm.author : [fm.author]).map((a) => {
            const m = String(a).trim().match(/^\[\[([^\]|]+)(?:\|([^\]]*))?\]\]$/);
            if (m) return m[1].includes("/") ? a : personLink(m[1].trim());
            const name = String(a).trim();
            return name ? personLink(name) : a;
          });
        }

        if (blank(fm.published) && published) fm.published = published;
        else if (!("published" in fm)) fm.published = null;

        if (blank(fm["date-added"])) {
          const created = fm.created && moment(String(fm.created)).isValid() ? moment(String(fm.created)) : moment(file.stat.ctime);
          fm["date-added"] = created.format();
        }

        // essay and blog-post are now article
        if (fm.type === "essay" || fm.type === "blog-post") fm.type = "article";
        if (blank(fm.type)) fm.type = type || null;

        if (fm.type === "book") fm.minutes = null; // books don't get a length estimate
        else if (blank(fm.minutes) && minutes) fm.minutes = minutes;
        else if (!("minutes" in fm)) fm.minutes = null;

        if (blank(fm.description) && description) fm.description = description;
        else if (!("description" in fm)) fm.description = null;

        let tags = blank(fm.tags) ? [] : Array.isArray(fm.tags) ? [...fm.tags] : String(fm.tags).split(/[,\s]+/).filter(Boolean);
        tags = tags.filter((t) => t !== "essay" && t !== "blog-post");
        if (!tags.includes("clippings")) tags.push("clippings");
        if (fm.type && !tags.includes(fm.type)) tags.push(fm.type);
        fm.tags = tags;
      });
      updated++;
    } catch (e) {
      console.error("Reading Backfill:", file.path, e);
      problems.push(`${file.basename}: ${e.message}`);
    }
  }

  status.hide();
  new Notice(
    stopped
      ? `Reading Backfill stopped: updated ${updated} of ${notes.length} notes`
      : `Reading Backfill: updated ${updated} of ${notes.length} notes`,
    10000
  );
  if (problems.length) await quickAddApi.infoDialog("Notes with problems", problems);
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
  if (h === "open.spotify.com") {
    if (/^\/(intl-[^/]+\/)?(track|album|playlist|artist)\//.test(path)) return "music";
    return "podcast";
  }
  if (/(^|\.)youtube\.com$|^youtu\.be$|(^|\.)vimeo\.com$/.test(h)) return "video";
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
    if (o.json.author_name) meta.authors.push(o.json.author_name);
  } catch (e) { console.warn("Link Ingest: oEmbed failed", e); }
  try {
    const html = (await requestUrl({ url, headers: { "Accept-Language": "en-US,en;q=0.9" } })).text;
    const desc = html.match(/"shortDescription":"((?:[^"\\]|\\.)*)"/);
    if (desc) meta.text = JSON.parse(`"${desc[1]}"`);
    const pub = html.match(/"(?:publishDate|uploadDate)":"([^"]+)"/);
    if (pub) meta.published = pub[1];
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
    '- "authors": array of the people who made it (use the organization if no person is credited); [] if unknown',
    '- "description": a 2-3 sentence plain summary of what it says or covers',
    '- "published": publication date as YYYY-MM-DD, or "" if unknown',
    '- "minutes": your estimate of how many minutes it takes to read, watch or listen to the full piece (a number; for a paper, the full paper, not just the abstract; for music, the track or album)',
    "",
    `URL: ${url}`,
    `Site: ${meta.siteName}`,
    `Metadata title: ${meta.title}`,
    `Metadata authors: ${meta.authors.join(", ")}`,
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
