```datacorejsx
const FOLDER = "content";
const OLD_DAYS = 30;

// ---------- helpers ----------

const toMs = (v) => {
  if (!v) return 0;
  if (typeof v === "number") return v;
  if (typeof v.toMillis === "function") return v.toMillis();
  const t = Date.parse(String(v));
  return isNaN(t) ? 0 : t;
};
const asList = (v) => (v == null || v === "" ? [] : Array.isArray(v) ? v : [v]);
const nameOf = (v) => {
  if (v && typeof v === "object") return String(v.display ?? v.path ?? "").split("/").pop().replace(/\.md$/, "");
  return String(v ?? "").replace(/^\[\[|\]\]$/g, "");
};

const get = (p, key) => p.value(key);
// Archived = date-archived is filled in (the date it was first archived)
const isDone = (p) => !!get(p, "date-archived");
const added = (p) => toMs(get(p, "date-added")) || toMs(p.$ctime);
const finished = (p) => toMs(get(p, "date-archived")) || toMs(p.$mtime);
const minutes = (p) => {
  const m = Number(get(p, "minutes"));
  return m > 0 ? m : null;
};

const TYPES = ["writing", "research", "book", "podcast", "video", "film", "show", "music", "art", "project"];
// Notes with no type, or a type not in the list, count as "writing"
const typeOf = (p) => {
  const t = get(p, "type");
  return TYPES.includes(t) ? t : "writing";
};
const lengthOf = (p) => {
  if (get(p, "type") === "book") return "Books";
  const m = minutes(p);
  if (m == null) return "Unknown length";
  if (m < 15) return "Short (under 15 min)";
  if (m <= 45) return "Medium (15–45 min)";
  return "Long (over 45 min)";
};

const byNewest = (a, b) => added(b) - added(a);
const byOldest = (a, b) => added(a) - added(b);
const byShortest = (a, b) => (minutes(a) ?? 1e9) - (minutes(b) ?? 1e9);

// Group rows by one or more keys per row; returns [[key, rows], ...]
function groupBy(rows, keyFn, order) {
  const map = new Map();
  for (const p of rows) {
    for (const k of asList(keyFn(p))) {
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(p);
    }
  }
  let entries = [...map.entries()];
  if (order) entries.sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
  else entries.sort((a, b) => b[1].length - a[1].length);
  return entries;
}

// ---------- table pieces ----------

// Obsidian adds this class to the page on phones and tablets
const IS_MOBILE = document.body.classList.contains("is-mobile");

const COLUMNS = [
  {
    id: "Title",
    value: (p) => p.$link,
    // Long titles wrap instead of widening the column (mostly matters on mobile)
    render: (link) => (
      <div style={{ minWidth: "8rem", maxWidth: IS_MOBILE ? "12rem" : "18rem" }}>
        <dc.Link link={link} />
      </div>
    ),
  },
  { id: "Type", value: (p) => [get(p, "type"), get(p, "form")].filter(Boolean).join(" · ") },
  {
    id: "URL",
    value: (p) => get(p, "url") ?? "",
    render: (url) => {
      if (!url) return "";
      let label = url;
      try { label = new URL(url).hostname.replace(/^www\./, ""); } catch (e) { /* show raw */ }
      return (
        <a
          href={url}
          className="external-link"
          title={url}
          style={{ display: "inline-block", maxWidth: "7rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", verticalAlign: "bottom" }}
        >
          {label}
        </a>
      );
    },
  },
  {
    id: "About",
    value: (p) => get(p, "description") ?? "",
    // Desktop: wraps, shows about 3 lines, and scrolls vertically for the rest.
    // Mobile: shows the full text, since a scrolling cell catches swipes meant for the page.
    render: (text) =>
      IS_MOBILE ? (
        <div style={{ minWidth: "12rem", maxWidth: "16rem" }}>{text}</div>
      ) : (
        <div style={{ maxWidth: "26rem", maxHeight: "4.5em", lineHeight: "1.5em", overflowY: "auto" }}>{text}</div>
      ),
  },
  { id: "Min", value: (p) => minutes(p) ?? "" },
];

function Rows({ rows }) {
  if (!rows.length) return <p><em>Nothing here.</em></p>;
  // Lets a table wider than the screen scroll sideways on its own.
  // Stopping touch events here keeps Obsidian from treating a sideways swipe
  // on the table as "open the sidebar".
  const stop = (e) => e.stopPropagation();
  return (
    <div
      className="reading-dash-scroll"
      style={{ overflowX: "auto", WebkitOverflowScrolling: "touch", maxWidth: "100%", touchAction: "pan-x pan-y" }}
      onTouchStart={stop}
      onTouchMove={stop}
    >
      <dc.Table rows={rows} columns={COLUMNS} />
    </div>
  );
}

function Groups({ groups }) {
  if (!groups.length) return <p><em>Nothing here.</em></p>;
  return (
    <div>
      {groups.map(([name, rows]) => (
        <div key={name}>
          <h4>{name} ({rows.length})</h4>
          <Rows rows={rows} />
        </div>
      ))}
    </div>
  );
}

function RandomPick({ rows }) {
  const [maxMin, setMaxMin] = dc.useState(null);
  const pool = maxMin ? rows.filter((p) => minutes(p) != null && minutes(p) <= maxMin) : rows;
  const [seed, setSeed] = dc.useState(Math.random());
  const pick = pool.length ? pool[Math.floor(seed * pool.length) % pool.length] : null;

  return (
    <div>
      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "10px" }}>
        <button onClick={() => setSeed(Math.random())}>Pick another</button>
        {[null, 15, 45].map((m) => (
          <button key={String(m)} className={maxMin === m ? "mod-cta" : ""} onClick={() => setMaxMin(m)}>
            {m ? `≤ ${m} min` : "Any length"}
          </button>
        ))}
      </div>
      {pick ? (
        <div>
          <h3><dc.Link link={pick.$link} /></h3>
          <p>
            {[get(pick, "type"), minutes(pick) ? `${minutes(pick)} min` : null, asList(get(pick, "creators")).map(nameOf).join(", ")]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <p>{get(pick, "description")}</p>
          {get(pick, "source") ? <p><em>Found via: {get(pick, "source")}</em></p> : null}
        </div>
      ) : (
        <p><em>Nothing matches.</em></p>
      )}
    </div>
  );
}

// ---------- views ----------

const VIEWS = ["Queue", "By type", "By length", "Getting old", "Random", "Recently finished"];
return function View() {
  const pages = dc.useQuery(`@page and path("${FOLDER}")`);
  const [view, setView] = dc.useState("Queue");
  const [type, setType] = dc.useState("writing");

  const unread = pages.filter((p) => !isDone(p));
  const cutoff = Date.now() - OLD_DAYS * 24 * 60 * 60 * 1000;

  let body;
  switch (view) {
    case "Queue":
      body = <Rows rows={[...unread].sort(byNewest)} />;
      break;
    case "By type":
      body = (
        <div>
          <select className="dropdown" value={type} onChange={(e) => setType(e.target.value)} style={{ marginBottom: "10px" }}>
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {t} ({unread.filter((p) => typeOf(p) === t).length})
              </option>
            ))}
          </select>
          <Rows rows={unread.filter((p) => typeOf(p) === type).sort(byNewest)} />
        </div>
      );
      break;
    case "By length":
      body = (
        <Groups
          groups={groupBy([...unread].sort(byShortest), lengthOf, [
            "Short (under 15 min)", "Medium (15–45 min)", "Long (over 45 min)", "Books", "Unknown length",
          ])}
        />
      );
      break;
    case "Getting old":
      body = <Rows rows={unread.filter((p) => added(p) < cutoff).sort(byOldest)} />;
      break;
    case "Random":
      body = <RandomPick rows={unread} />;
      break;
    case "Recently finished":
      body = <Rows rows={pages.filter(isDone).sort((a, b) => finished(b) - finished(a)).slice(0, 20)} />;
      break;
  }

  return (
    <div>
      {/* On mobile, tables keep their natural width instead of squeezing to fit, so they can scroll sideways */}
      {IS_MOBILE ? <style>{`.reading-dash-scroll table { width: max-content; min-width: 100%; }`}</style> : null}
      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "12px" }}>
        {VIEWS.map((v) => (
          <button key={v} className={v === view ? "mod-cta" : ""} onClick={() => setView(v)}>
            {v}
          </button>
        ))}
      </div>
      <p style={{ color: "var(--text-muted)" }}>{unread.length} unread · {pages.length - unread.length} finished</p>
      {body}
    </div>
  );
};
```
