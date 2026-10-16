// web/static/js/dashboard.js
// Vanilla ES module for the study dashboard. No build tools, no frameworks.
// All requests use credentials: "same-origin" so the session cookie
// issued by /api/v1/auth/google/callback is included automatically.
//
// Data sources (all read-only, all user-scoped server-side):
//   GET /api/v1/search?q=...           -> entries (filtered list, or all when q is empty)
//   GET /api/v1/digest                 -> today's pending reviews
//   GET /api/v1/reviews/upcoming?days=N -> pending reviews for the next N days, bucketed by date

(function () {
  "use strict";

  const SEARCH_API = "/api/v1/search";
  const DIGEST_API = "/api/v1/digest";
  const UPCOMING_API = "/api/v1/reviews/upcoming";

  const entriesStatus = document.getElementById("entries-status");
  const upcomingStatus = document.getElementById("upcoming-status");
  const entriesRoot = document.getElementById("entries-root");
  const upcomingRoot = document.getElementById("upcoming-root");
  const statTotalEntries = document.getElementById("stat-total-entries");
  const statDueToday = document.getElementById("stat-due-today");
  const statUpcomingTotal = document.getElementById("stat-upcoming-total");
  const searchInput = document.getElementById("entry-search");
  const upcomingWindow = document.getElementById("upcoming-window");

  if (!entriesRoot || !upcomingRoot) return;

  // Debounce input so each keystroke doesn't fire a request -- wait until the
  // user pauses typing for ~200ms before hitting the search endpoint.
  function debounce(fn, wait) {
    let timer = null;
    return function debounced(...args) {
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => fn.apply(null, args), wait);
    };
  }

  async function jsonFetch(url, init) {
    return window.sraJsonFetch(url, init);
  }

  function setStatus(node, text, kind) {
    if (!node) return;
    node.textContent = text;
    node.className = "lede" + (kind ? " " + kind : " muted");
  }

  function el(tag, attrs, text) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const key of Object.keys(attrs)) {
        if (key === "class") node.className = attrs[key];
        else if (key === "dataset") Object.assign(node.dataset, attrs[key]);
        else node.setAttribute(key, attrs[key]);
      }
    }
    if (text != null) node.appendChild(document.createTextNode(String(text)));
    return node;
  }

  function panel(kind, attrs) {
    return el("section", { class: "panel " + kind, ...(attrs || {}) });
  }

  function describeError(res) {
    if (res.status === 401) return "Your session expired. Please sign in again.";
    if (res.status === 403) return "You don't have access to that resource.";
    if (res.status === 429) return "Too many requests. Please slow down.";
    return `The server returned ${res.status}.`;
  }

  function formatDate(value) {
    // ISO date string (YYYY-MM-DD) -> "Mon, Jan 5, 2026"
    try {
      const d = new Date(value + "T00:00:00Z");
      return d.toLocaleDateString(undefined, {
        weekday: "short",
        year: "numeric",
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      });
    } catch (err) {
      return String(value);
    }
  }

  function formatDateTime(value) {
    try {
      const d = new Date(value);
      return d.toLocaleString();
    } catch (err) {
      return String(value);
    }
  }

  function relativeDayLabel(targetIsoDate, todayIsoDate) {
    if (targetIsoDate === todayIsoDate) return "Today";
    // Compute the day delta from today (UTC midnight) to the target date.
    const target = new Date(targetIsoDate + "T00:00:00Z").getTime();
    const today = new Date(todayIsoDate + "T00:00:00Z").getTime();
    const days = Math.round((target - today) / 86400000);
    if (days === 1) return "Tomorrow";
    if (days > 1 && days < 7) return `In ${days} days`;
    if (days === 7) return "In 1 week";
    if (days > 7 && days < 14) return `In ${days} days`;
    if (days === 14) return "In 2 weeks";
    if (days > 14 && days < 30) return `In ${days} days`;
    if (days === 30) return "In 1 month";
    if (days > 30 && days < 60) return `In ${days} days`;
    if (days === 60) return "In 2 months";
    return `In ${days} days`;
  }

  function todayIsoDate() {
    // Anchor "Today" / "Tomorrow" labels on the same UTC midnight the server uses.
    return new Date().toISOString().slice(0, 10);
  }

  // --- entries section -----------------------------------------------------

  function renderEntriesEmpty() {
    setStatus(entriesStatus, "No entries match your search yet.");
    entriesRoot.replaceChildren(
      panel("empty", null, el("p", null, "Capture a study note through the menu-bar app or POST /api/v1/entries/ingest to see it here."))
    );
  }

  function renderEntries(entries) {
    if (!Array.isArray(entries) || entries.length === 0) {
      renderEntriesEmpty();
      return;
    }
    setStatus(entriesStatus, `${entries.length} ${entries.length === 1 ? "entry" : "entries"}.`);
    const list = panel("entries-list");
    for (const entry of entries) {
      list.appendChild(renderEntryCard(entry));
    }
    entriesRoot.replaceChildren(list);
  }

  function renderEntryCard(entry) {
    const card = el("article", { class: "entry-card", dataset: { entryId: String(entry.id) } });

    const header = el("header", { class: "entry-card-header" });
    header.appendChild(el("h3", { class: "entry-topic" }, entry.topic));
    if (entry.subtopic) {
      header.appendChild(el("p", { class: "entry-subtopic muted" }, entry.subtopic));
    }
    card.appendChild(header);

    if (Array.isArray(entry.tags) && entry.tags.length > 0) {
      const tags = el("ul", { class: "entry-tags" });
      for (const tag of entry.tags) {
        tags.appendChild(el("li", { class: "entry-tag" }, tag));
      }
      card.appendChild(tags);
    }

    card.appendChild(el("p", { class: "entry-summary" }, entry.summary));

    const meta = el("p", { class: "entry-meta muted" });
    meta.appendChild(document.createTextNode("Captured "));
    meta.appendChild(el("time", { datetime: entry.created_at }, formatDateTime(entry.created_at)));
    card.appendChild(meta);

    const details = el("details", { class: "entry-details" });
    const summary = el("summary", null, "Show original transcript");
    details.appendChild(summary);
    details.appendChild(
      el("pre", { class: "entry-transcript" }, entry.raw_transcript || "(no transcript)")
    );
    card.appendChild(details);

    return card;
  }

  async function loadEntries(query) {
    setStatus(entriesStatus, query ? `Searching for “${query}”…` : "Loading entries…");
    const url = SEARCH_API + "?q=" + encodeURIComponent(query || "");
    const res = await jsonFetch(url);
    if (res.status === 401) {
      window.location.href = "/signin?next=/dashboard";
      return;
    }
    if (!res.ok) {
      setStatus(entriesStatus, describeError(res), "error");
      return;
    }
    const entries = await res.json();
    renderEntries(entries);
    // Stats tile "Total entries" mirrors the unfiltered count, so re-query
    // once on first load without a query to populate it accurately.
    if (statTotalEntries.textContent === "—") {
      const all = await jsonFetch(SEARCH_API);
      if (all.ok) {
        const allEntries = await all.json();
        statTotalEntries.textContent = String(Array.isArray(allEntries) ? allEntries.length : 0);
      } else {
        statTotalEntries.textContent = String(Array.isArray(entries) ? entries.length : 0);
      }
    }
  }

  // --- upcoming reviews section -------------------------------------------

  function renderUpcomingEmpty() {
    setStatus(upcomingStatus, "No upcoming reviews in this window.");
    upcomingRoot.replaceChildren(
      panel("empty", null, el("p", null, "Capture a study note to schedule its 1d/3d/7d/1mo/3mo/6mo reviews."))
    );
  }

  function renderUpcoming(days) {
    if (statUpcomingTotal.textContent !== "—") statUpcomingTotal.textContent = "—";
    setStatus(upcomingStatus, `Loading reviews for the next ${days} days…`);
    const res = jsonFetch(UPCOMING_API + "?days=" + encodeURIComponent(String(days)))
      .then(async (r) => {
        if (r.status === 401) {
          window.location.href = "/signin?next=/dashboard";
          return;
        }
        if (!r.ok) {
          setStatus(upcomingStatus, describeError(r), "error");
          return;
        }
        const grouped = await r.json();
        if (!Array.isArray(grouped) || grouped.length === 0) {
          renderUpcomingEmpty();
          return;
        }
        const today = todayIsoDate();
        let totalUpcoming = 0;
        const wrap = panel("upcoming-days");
        for (const day of grouped) {
          totalUpcoming += Array.isArray(day.reviews) ? day.reviews.length : 0;
          wrap.appendChild(renderUpcomingDay(day, today));
        }
        statUpcomingTotal.textContent = String(totalUpcoming);
        setStatus(upcomingStatus, `${totalUpcoming} ${totalUpcoming === 1 ? "review" : "reviews"} across ${grouped.length} ${grouped.length === 1 ? "day" : "days"}.`);
        upcomingRoot.replaceChildren(wrap);
      })
      .catch((err) => {
        setStatus(upcomingStatus, "Network error: " + (err && err.message ? err.message : String(err)), "error");
      });
  }

  function renderUpcomingDay(day, today) {
    const tile = el("article", { class: "upcoming-day" });
    const head = el("header", { class: "upcoming-day-header" });
    head.appendChild(el("span", { class: "upcoming-day-date" }, formatDate(day.date)));
    head.appendChild(el("span", { class: "upcoming-day-relative muted" }, relativeDayLabel(day.date, today)));
    tile.appendChild(head);

    const list = el("ul", { class: "upcoming-day-list" });
    for (const review of day.reviews || []) {
      const li = el("li", { class: "upcoming-review" });
      li.appendChild(el("span", { class: "upcoming-review-interval" }, review.interval_label));
      li.appendChild(el("span", { class: "upcoming-review-topic" }, review.entry?.topic || "Untitled"));
      if (review.entry?.subtopic) {
        li.appendChild(el("span", { class: "upcoming-review-subtopic muted" }, "— " + review.entry.subtopic));
      }
      list.appendChild(li);
    }
    tile.appendChild(list);
    return tile;
  }

  async function loadDueToday() {
    const res = await jsonFetch(DIGEST_API);
    if (!res.ok) {
      statDueToday.textContent = "—";
      return;
    }
    const items = await res.json();
    statDueToday.textContent = String(Array.isArray(items) ? items.length : 0);
  }

  // --- wire up ------------------------------------------------------------

  const debouncedSearch = debounce((value) => loadEntries(value), 200);
  if (searchInput) {
    searchInput.addEventListener("input", (event) => {
      debouncedSearch(event.target.value);
    });
  }
  if (upcomingWindow) {
    upcomingWindow.addEventListener("change", (event) => {
      const days = Number(event.target.value) || 30;
      renderUpcoming(days);
    });
  }

  loadEntries("");
  loadDueToday();
  renderUpcoming(Number((upcomingWindow && upcomingWindow.value) || 30));
})();