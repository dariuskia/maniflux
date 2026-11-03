// Keyboard shortcuts and the "save with note" box. Everything else is htmx.
(() => {
  const page = document.body.dataset.page;
  const typing = (el) => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));

  function toggleNote(scope) {
    const form = scope.querySelector("form.note");
    if (!form) return;
    form.hidden = !form.hidden;
    if (!form.hidden) form.querySelector("textarea").focus();
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".note-toggle");
    if (btn) toggleNote(btn.closest(".card, .reader"));
  });

  const press = (scope, key) => scope?.querySelector(`[data-key="${key}"]`)?.click();

  // The list view you came from (search, tab, group, budget), so the reading view can go back to it
  const listUrl = () => {
    const u = new URL(sessionStorage.getItem("maniflux-list") || "/", location.origin);
    u.searchParams.delete("msg");
    return u.pathname + u.search;
  };
  const rememberList = () => sessionStorage.setItem("maniflux-list", location.pathname + location.search);

  // The last item archived, however it happened (Archive button, opening it, or v), so u can undo it
  const LAST = "maniflux-last-archived";
  // The item last opened from the list and where it sat, so coming back can show "Archived: …" in its place
  const OPENED = "maniflux-opened";

  // ---------- list ----------
  // The list and the archive share selection, keys, search and scroll memory
  if (page === "list" || page === "archive") {
    const cards = () => [...document.querySelectorAll("#cards > .card")];
    let current = null;

    function undoLast() {
      const id = sessionStorage.getItem(LAST);
      if (!id) return;
      sessionStorage.removeItem(LAST);
      const el = document.getElementById(`e-${id}`);
      if (el?.classList.contains("done")) {
        // Archived on this page: its Undo button is right there
        el.querySelector(".undo")?.click();
      } else if (el?.classList.contains("card")) {
        // Opened with v: the dimmed card is still here
        fetch(`/e/${id}/undo`, { method: "POST", headers: { "HX-Request": "true" } })
          .then((r) => r.text())
          .then((html) => {
            el.outerHTML = html;
            const card = document.getElementById(`e-${id}`);
            htmx.process(card);
            current = null;
            select(card, false);
          });
      } else {
        // Opened in the reading view, so it's no longer in the list: unarchive and reload, keeping the place
        fetch(`/e/${id}/undo`, { method: "POST", headers: { "HX-Request": "true" } }).then(() => {
          sessionStorage.setItem("maniflux-current", id);
          location.reload();
        });
      }
    }

    function select(card, scroll = true) {
      if (!card || card === current) return;
      current?.classList.remove("current");
      current = card;
      card.classList.add("current");
      if (scroll) card.scrollIntoView({ block: "nearest" });
      sessionStorage.setItem("maniflux-current", card.dataset.id);
    }

    const onScreen = (el) => {
      const r = el.getBoundingClientRect();
      return r.bottom > 0 && r.top < window.innerHeight;
    };

    function move(step) {
      const list = cards();
      if (!list.length) return;
      const i = list.indexOf(current);
      select(list[Math.min(list.length - 1, Math.max(0, i === -1 ? 0 : i + step))]);
    }

    function noteOpening(card) {
      if (!card) return;
      let next = card.nextElementSibling;
      while (next && !next.classList.contains("card")) next = next.nextElementSibling;
      let prev = card.previousElementSibling;
      while (prev && !prev.matches(".card, .done")) prev = prev.previousElementSibling;
      sessionStorage.setItem(OPENED, JSON.stringify({
        id: card.dataset.id,
        title: card.querySelector(".title")?.textContent.trim() || "",
        next: next?.dataset.id,
        prev: prev?.dataset.id,
        list: location.pathname + location.search,
      }));
    }
    document.addEventListener("click", (e) => {
      const a = e.target.closest("#cards > .card a.title");
      if (a) noteOpening(a.closest(".card"));
    });

    const withoutMsg = (url) => {
      const u = new URL(url, location.origin);
      u.searchParams.delete("msg");
      return u.pathname + u.search;
    };

    // Back from the reading view: the opened item was archived, so show it as archived where it was.
    // Returns the card after it, which should be selected.
    function showOpened() {
      const o = JSON.parse(sessionStorage.getItem(OPENED) || "null");
      if (!o?.archived) return null;
      sessionStorage.removeItem(OPENED);
      const box = document.getElementById("cards");
      if (!box || withoutMsg(location.href) !== withoutMsg(o.list)) return null;

      const row = document.createElement("div");
      row.className = "done";
      row.id = `e-${o.id}`;
      row.dataset.id = o.id;
      const text = document.createElement("span");
      text.textContent = o.title ? `Archived: ${o.title}` : "Archived";
      const undo = document.createElement("button");
      undo.className = "undo";
      undo.textContent = "Undo";
      undo.setAttribute("hx-post", `/e/${o.id}/undo`);
      undo.setAttribute("hx-target", `#e-${o.id}`);
      undo.setAttribute("hx-swap", "outerHTML");
      row.append(text, undo);

      // Still there (Back button with a cached page), or gone (fresh load): put the row where the card was
      const existing = document.getElementById(`e-${o.id}`);
      const next = o.next ? document.getElementById(`e-${o.next}`) : null;
      const prev = o.prev ? document.getElementById(`e-${o.prev}`) : null;
      if (existing) existing.replaceWith(row);
      else if (next) box.insertBefore(row, next);
      else if (prev) prev.after(row);
      else box.prepend(row);
      box.querySelector(":scope > .empty")?.remove();
      htmx.process(row);
      if (existing === current) current = null;
      const after = next?.isConnected ? next : null;
      if (after) sessionStorage.setItem("maniflux-current", after.dataset.id);
      return after;
    }
    if (page === "list") showOpened();
    // Back button showing the page from the browser's cache: scripts don't run again, but this does
    window.addEventListener("pageshow", (e) => {
      if (!e.persisted || page !== "list") return;
      const after = showOpened();
      if (after) select(after, false);
    });

    // Coming back to the list keeps the page where it was, per tab, group and time budget
    const view = new URLSearchParams(location.search);
    view.delete("msg");
    let scrollKey = `maniflux-scroll:${location.pathname}?${view}`;
    rememberList();
    history.scrollRestoration = "manual";
    window.scrollTo(0, Number(sessionStorage.getItem(scrollKey)) || 0);
    window.addEventListener("pagehide", () => sessionStorage.setItem(scrollKey, String(window.scrollY)));

    // Select the card you were on if it's on screen, else the first card on screen; don't move the page
    const saved = document.getElementById(`e-${sessionStorage.getItem("maniflux-current")}`);
    select(saved && onScreen(saved) ? saved : cards().find(onScreen) || cards()[0], false);

    // Hovering selects a card. Only real mouse movement counts, so cards sliding under a still
    // pointer while scrolling or pressing j/k don't take the selection.
    let lastPointer = null;
    document.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "mouse") return;
      const at = `${e.screenX},${e.screenY}`;
      if (at === lastPointer) return;
      lastPointer = at;
      const card = e.target.closest("#cards > .card");
      if (card) select(card, false);
    });

    // After a card turns into a "done" row, move on to the next card
    document.body.addEventListener("htmx:afterSwap", (e) => {
      const el = e.detail.elt?.id ? document.getElementById(e.detail.elt.id) : null;
      if (!el) return;
      if (el.id === "results") {
        // New search results: start from the first one, and remember this view (htmx has updated the address)
        rememberList();
        const v = new URLSearchParams(location.search);
        v.delete("msg");
        scrollKey = `maniflux-scroll:${location.pathname}?${v}`;
        current = null;
        select(cards()[0], false);
        return;
      }
      if (el.classList.contains("done")) {
        if (el.querySelector(".undo")) sessionStorage.setItem(LAST, el.dataset.id);
        if (!current || !current.isConnected) {
          let next = el.nextElementSibling;
          while (next && !next.classList.contains("card")) next = next.nextElementSibling;
          let prev = el.previousElementSibling;
          while (prev && !prev.classList.contains("card")) prev = prev.previousElementSibling;
          current = null;
          select(next || prev);
        }
      } else if (el.classList.contains("card")) {
        // Undone: nothing left for u to undo for this item
        if (sessionStorage.getItem(LAST) === el.dataset.id) sessionStorage.removeItem(LAST);
        select(el);
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (typing(e.target)) {
        if (e.target.matches(".search input") && e.key === "Enter") {
          // Results are already showing; go to them so j/k, s and x work
          e.preventDefault();
          e.target.blur();
          const first = cards()[0];
          if (first) { current = null; select(first); }
        } else if (e.key === "Escape") {
          e.target.closest("form.note")?.setAttribute("hidden", "");
          e.target.blur();
        }
        return;
      }
      const map = {
        j: () => move(1),
        k: () => move(-1),
        o: () => {
          if (!current) return;
          noteOpening(current);
          location.href = `/e/${current.dataset.id}`;
        },
        v: () => {
          if (!current) return;
          window.open(`/e/${current.dataset.id}/original`, "_blank", "noopener");
          current.classList.add("seen"); // opening the original archives it; it leaves the list on reload
          sessionStorage.setItem(LAST, current.dataset.id);
        },
        s: () => press(current, "s"),
        S: () => current && toggleNote(current),
        x: () => press(current, "x"),
        // On the list u undoes the last archive; on the archive page it unarchives the selected item
        u: page === "archive" ? () => press(current, "u") : undoLast,
        "/": () => {
          const box = document.querySelector(".search input");
          box?.focus();
          box?.select();
        },
      };
      // 1–4 switch tabs (All, Read, Watch, Listen)
      for (const n of ["1", "2", "3", "4"]) map[n] = () => press(document, n);
      if (map[e.key]) {
        e.preventDefault();
        map[e.key]();
      }
    });
  }

  // ---------- reading view ----------
  if (page === "entry") {
    const reader = document.querySelector(".reader");
    const back = listUrl();
    document.querySelectorAll("a.back").forEach((a) => (a.href = back));
    document.querySelectorAll('input[name="back"]').forEach((i) => (i.value = back));
    if (reader?.dataset.archived) {
      sessionStorage.setItem(LAST, reader.dataset.id);
      const o = JSON.parse(sessionStorage.getItem(OPENED) || "null");
      if (o?.id === reader.dataset.id) sessionStorage.setItem(OPENED, JSON.stringify({ ...o, archived: true }));
    } else {
      sessionStorage.removeItem(OPENED);
    }
    // Saving or unarchiving from here shows its own message on the list instead
    document.querySelectorAll(".reader form").forEach((f) => f.addEventListener("submit", () => sessionStorage.removeItem(OPENED)));
    document.querySelector('form[action$="/unread"]')?.addEventListener("submit", () => sessionStorage.removeItem(LAST));
    document.addEventListener("keydown", (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || !reader) return;
      if (typing(e.target)) {
        if (e.key === "Escape") e.target.blur();
        return;
      }
      const map = {
        v: () => press(reader, "v"),
        s: () => press(reader, "s"),
        S: () => toggleNote(reader),
        u: () => press(reader, "u"),
        Escape: () => (location.href = back),
        b: () => (location.href = back),
      };
      if (map[e.key]) {
        e.preventDefault();
        map[e.key]();
      }
    });
  }
})();
