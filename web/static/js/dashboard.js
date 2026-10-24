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
  const TOPICS_API = "/api/v1/topics";
  const QUIZ_API = "/api/v1/quiz";

  const entriesStatus = document.getElementById("entries-status");
  const upcomingStatus = document.getElementById("upcoming-status");
  const entriesRoot = document.getElementById("entries-root");
  const upcomingRoot = document.getElementById("upcoming-root");
  const statTotalEntries = document.getElementById("stat-total-entries");
  const statDueToday = document.getElementById("stat-due-today");
  const statUpcomingLabel = document.getElementById("stat-upcoming-label");
  const statUpcomingTotal = document.getElementById("stat-upcoming-total");
  const searchInput = document.getElementById("entry-search");
  const upcomingWindow = document.getElementById("upcoming-window");

  if (!entriesRoot || !upcomingRoot) return;

  const model = window.sraLibraryModel;
  if (!model) return;
  const shelfPrev = document.querySelector('[data-action="shelf-prev"]');
  const shelfNext = document.querySelector('[data-action="shelf-next"]');
  const shelfPosition = document.querySelector("[data-shelf-position]");
  const topicSort = document.getElementById("topic-sort");
  const state = {
    entries: [], due: [], upcoming: [], groups: [], visible: [],
    query: "", filter: "all", sort: "next", index: 0,
    generationResults: new Map(), quizSets: [],
  };

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

  function reviewCountWithin(days) {
    const end = new Date(todayIsoDate() + "T00:00:00Z");
    end.setUTCDate(end.getUTCDate() + days);
    const endIso = end.toISOString().slice(0, 10);
    return state.upcoming.reduce((total, day) => total +
      (day.date <= endIso ? (Array.isArray(day.reviews) ? day.reviews.length : 0) : 0), 0);
  }

  function updateUpcomingStats() {
    const days = Number((upcomingWindow && upcomingWindow.value) || 30);
    const total = reviewCountWithin(days);
    if (statUpcomingLabel) statUpcomingLabel.textContent = `Upcoming (next ${days} days)`;
    statUpcomingTotal.textContent = String(total);
  }

  function renderNote(entry) {
    const note = el("article", { class: "topic-note", dataset: { entryId: String(entry.id) } });
    const meta = el("p", { class: "entry-meta muted" }, "Captured ");
    meta.appendChild(el("time", { datetime: entry.created_at }, formatDateTime(entry.created_at)));
    note.appendChild(meta);
    if (entry.summary) note.appendChild(el("p", { class: "entry-summary" }, entry.summary));
    const details = el("details", { class: "entry-details" });
    details.appendChild(el("summary", null, "Show original transcript"));
    details.appendChild(el("pre", { class: "entry-transcript" }, entry.raw_transcript || "(no transcript)"));
    note.appendChild(details);
    const actions = el("p", { class: "entry-actions" });
    const result = state.generationResults.get(String(entry.id));
    const quizSetId = result && (result.quiz_set_id || (result.quiz_set && result.quiz_set.id));
    const interviewPlanId = result && (result.interview_plan_id || (result.interview_plan && result.interview_plan.id));
    if (quizSetId) actions.appendChild(el("a", { class: "button", href: "/quiz?quiz_set_id=" + quizSetId }, "Quiz me"));
    if (quizSetId && interviewPlanId) actions.appendChild(el("a", { class: "button", href: "/dashboard/interview?quiz_set_id=" + quizSetId }, "Start interview"));
    const button = el("button", { type: "button", class: "button", dataset: { action: "regenerate" } }, "Regenerate");
    button.addEventListener("click", () => onRegenerate(note, entry.id, button));
    actions.appendChild(button);
    note.appendChild(actions);
    return note;
  }

  function renderTopicCard(group) {
    const card = el("article", {
      class: "entry-card topic-card", dataset: { topicKey: group.key },
      role: "button", tabindex: "0", "aria-label": `Edit ${group.topic}`,
    });
    const header = el("header", { class: "topic-card-header" });
    const titleWrap = el("div");
    titleWrap.appendChild(el("p", { class: "topic-kicker muted", dataset: { field: "note-count" } },
      `${group.noteCount} ${group.noteCount === 1 ? "note" : "notes"}`));
    titleWrap.appendChild(el("h3", { class: "entry-topic" }, group.topic));
    header.appendChild(titleWrap);
    if (group.readyNow) header.appendChild(el("span", { class: "ready-badge" }, "Ready now"));
    card.appendChild(header);
    card.appendChild(el("p", { class: "entry-summary" }, group.summary || "A quiet place to continue learning."));
    if (group.tags.length) {
      const tags = el("ul", { class: "entry-tags" });
      for (const tag of group.tags) tags.appendChild(el("li", { class: "entry-tag" }, tag));
      card.appendChild(tags);
    }
    const dates = el("div", { class: "review-date-chips", "aria-label": "Review dates" });
    for (const item of group.reviewDates) {
      dates.appendChild(el("span", { class: "review-date-chip", dataset: { count: String(item.count) } },
        `${formatDate(item.date)}${item.count > 1 ? ` · ${item.count}` : ""}`));
    }
    if (!group.reviewDates.length) dates.appendChild(el("span", { class: "review-date-chip is-quiet" }, "No review scheduled"));
    card.appendChild(dates);
    const cardActions = el("div", { class: "topic-card-actions" });
    // Stable selector contract: data-action: "edit-topic".
    const edit = el("button", { type: "button", class: "button", "data-action": "edit-topic" }, "Edit");
    edit.addEventListener("click", (event) => { event.stopPropagation(); openEntryEditor(group.entries[0], card); });
    cardActions.appendChild(edit);
    const entryIds = new Set(group.entries.map((entry) => String(entry.id)));
    const quizSet = state.quizSets.find((item) => item.entry && entryIds.has(String(item.entry.id)));
    if (quizSet) {
      const quiz = el("a", { class: "button primary", href: "/quiz?quiz_set_id=" + quizSet.id }, "Quiz me");
      quiz.addEventListener("click", (event) => event.stopPropagation());
      cardActions.appendChild(quiz);
    }
    card.appendChild(cardActions);
    const notesId = "topic-notes-" + group.entries[0].id;
    const open = el("button", { type: "button", class: "notes-toggle", dataset: { action: "open-notes" }, "aria-expanded": "false", "aria-controls": notesId }, `Open ${group.noteCount} ${group.noteCount === 1 ? "note" : "notes"}`);
    const notes = el("div", { class: "topic-notes", id: notesId, hidden: true });
    for (const entry of group.entries) notes.appendChild(renderNote(entry));
    open.addEventListener("click", (event) => {
      event.stopPropagation();
      const expanded = open.getAttribute("aria-expanded") === "true";
      open.setAttribute("aria-expanded", String(!expanded));
      open.textContent = expanded ? `Open ${group.noteCount} ${group.noteCount === 1 ? "note" : "notes"}` : "Close notes";
      notes.hidden = expanded;
    });
    card.appendChild(open);
    card.appendChild(notes);
    card.addEventListener("click", (event) => {
      if (event.target.closest("button, a, details, input, select, textarea")) return;
      openEntryEditor(group.entries[0], card);
    });
    card.addEventListener("keydown", (event) => {
      if ((event.key === "Enter" || event.key === " ") && event.target === card) {
        event.preventDefault();
        openEntryEditor(group.entries[0], card);
      }
    });
    return card;
  }

  function updateShelfControls() {
    const total = state.visible.length;
    if (total === 0) state.index = 0;
    else state.index = Math.min(state.index, total - 1);
    if (shelfPosition) shelfPosition.textContent = total ? `${state.index + 1} of ${total}` : "0 of 0";
    if (shelfPrev) shelfPrev.disabled = state.index <= 0;
    if (shelfNext) shelfNext.disabled = !total || state.index >= total - 1;
  }

  function renderShelf() {
    state.visible = model.filterAndSortTopics(state.groups, { query: state.query, filter: state.filter, sort: state.sort }, todayIsoDate());
    state.index = 0;
    if (!state.visible.length) {
      entriesRoot.replaceChildren(panel("empty", null, el("p", null, "No topics match this view yet.")));
      setStatus(entriesStatus, "No topics match this view.");
    } else {
      entriesRoot.replaceChildren(...state.visible.map(renderTopicCard));
      const notes = state.visible.reduce((sum, group) => sum + group.noteCount, 0);
      setStatus(entriesStatus, `${state.visible.length} ${state.visible.length === 1 ? "topic" : "topics"} · ${notes} ${notes === 1 ? "note" : "notes"}.`);
    }
    updateShelfControls();
  }

  function renderHorizon() {
    const horizon = model.buildReviewHorizon(state.groups, todayIsoDate());
    const labels = { ready: "Ready now", next7: "Next 7 days", next30: "Next 30 days", later: "Later" };
    const descriptions = { ready: "A gentle place to begin", next7: "Coming into view", next30: "Space to prepare", later: "Resting for now" };
    const nodes = Object.keys(labels).map((key) => {
      const button = el("button", { type: "button", class: "horizon-card", dataset: { horizon: key } });
      button.appendChild(el("span", { class: "horizon-count", dataset: { field: "count" } }, horizon[key].count));
      button.appendChild(el("strong", null, labels[key]));
      button.appendChild(el("span", { class: "muted" }, descriptions[key]));
      button.addEventListener("click", () => setFilter(key));
      return button;
    });
    upcomingRoot.replaceChildren(...nodes);
    setStatus(upcomingStatus, "Each topic appears once, at its nearest review moment.");
    updateUpcomingStats();
  }

  function rebuild() {
    state.groups = model.buildTopicGroups(state.entries, state.due, state.upcoming, todayIsoDate());
    statTotalEntries.textContent = String(state.entries.length);
    statDueToday.textContent = String(state.due.length);
    renderShelf();
    renderHorizon();
  }

  function setFilter(filter) {
    state.filter = filter;
    document.querySelectorAll("[data-filter]").forEach((button) => {
      const active = button.dataset.filter === filter;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    renderShelf();
  }

  function moveShelf(delta) {
    if (!state.visible.length) return;
    state.index = Math.max(0, Math.min(state.visible.length - 1, state.index + delta));
    entriesRoot.querySelectorAll(".topic-card")[state.index]?.scrollIntoView({ behavior: "smooth", inline: "start", block: "nearest" });
    updateShelfControls();
  }

  async function loadDashboard() {
    setStatus(entriesStatus, "Loading entries…");
    setStatus(upcomingStatus, "Loading upcoming reviews…");
    try {
      const [entriesRes, dueRes, upcomingRes, quizRes] = await Promise.all([
        jsonFetch(SEARCH_API), jsonFetch(DIGEST_API), jsonFetch(UPCOMING_API + "?days=365"),
        jsonFetch(QUIZ_API + "/today"),
      ]);
      if ([entriesRes, dueRes, upcomingRes].some((res) => res.status === 401)) {
        window.location.href = "/signin?next=/dashboard";
        return;
      }
      if (!entriesRes.ok || !dueRes.ok || !upcomingRes.ok) throw new Error("The dashboard could not be loaded.");
      state.entries = await entriesRes.json();
      state.due = await dueRes.json();
      state.upcoming = await upcomingRes.json();
      state.quizSets = quizRes.ok ? await quizRes.json() : [];
      rebuild();
    } catch (err) {
      setStatus(entriesStatus, err && err.message ? err.message : String(err), "error");
      setStatus(upcomingStatus, "Review timing is temporarily unavailable.", "error");
    }
  }

  searchInput?.addEventListener("input", (event) => { state.query = event.target.value; renderShelf(); });
  topicSort?.addEventListener("change", (event) => { state.sort = event.target.value; renderShelf(); });
  document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => setFilter(button.dataset.filter)));
  shelfPrev?.addEventListener("click", () => moveShelf(-1));
  shelfNext?.addEventListener("click", () => moveShelf(1));
  entriesRoot.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      moveShelf(event.key === "ArrowLeft" ? -1 : 1);
    }
  });
  upcomingWindow?.addEventListener("change", updateUpcomingStats);

  function openEntryEditor(entry, trigger) {
    const backdrop = el("div", {
      class: "modal-backdrop", role: "dialog", "aria-modal": "true",
      "aria-labelledby": "edit-entry-title",
    });
    const modal = el("div", { class: "modal" });
    const header = el("header", { class: "modal-header" });
    header.appendChild(el("h2", { id: "edit-entry-title" }, "Edit note"));
    const closeButton = el("button", { type: "button", class: "link-button", dataset: { action: "close-modal" } }, "Close");
    header.appendChild(closeButton);
    modal.appendChild(header);
    const form = el("form", { class: "modal-form" });
    const topicField = el("div", { class: "modal-field" });
    topicField.appendChild(el("label", { for: "edit-entry-topic" }, "Topic"));
    const topicInput = el("input", { id: "edit-entry-topic", type: "text", required: "true", maxlength: "200", value: entry.topic });
    topicField.appendChild(topicInput);
    form.appendChild(topicField);
    const dateField = el("div", { class: "modal-field" });
    dateField.appendChild(el("label", { for: "edit-entry-date" }, "Captured date and time"));
    const dateInput = el("input", { id: "edit-entry-date", type: "datetime-local", required: "true", value: String(entry.created_at).slice(0, 16) });
    dateField.appendChild(dateInput);
    form.appendChild(dateField);
    const status = el("p", { class: "modal-status", role: "status", "aria-live": "polite" });
    form.appendChild(status);
    const actions = el("div", { class: "modal-actions" });
    // Stable selector contract: data-action: "delete-entry".
    const remove = el("button", { type: "button", class: "button edit-modal-danger", dataset: { action: "delete-entry" } }, "Delete note");
    const cancel = el("button", { type: "button", class: "button" }, "Cancel");
    const save = el("button", { type: "submit", class: "button primary" }, "Save changes");
    actions.appendChild(remove); actions.appendChild(cancel); actions.appendChild(save);
    form.appendChild(actions); modal.appendChild(form); backdrop.appendChild(modal); document.body.appendChild(backdrop);

    const close = () => { document.removeEventListener("keydown", onKeydown); backdrop.remove(); trigger.focus(); };
    const onKeydown = (event) => {
      if (event.key === "Escape") close();
      if (event.key !== "Tab") return;
      const focusable = Array.from(modal.querySelectorAll("button, input"));
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeydown);
    backdrop.addEventListener("click", (event) => { if (event.target === backdrop) close(); });
    closeButton.addEventListener("click", close); cancel.addEventListener("click", close);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const topic = topicInput.value.trim();
      if (!topic) { status.textContent = "Topic is required."; return; }
      save.disabled = true; status.textContent = "Saving…";
      const response = await jsonFetch(`/api/v1/entries/${entry.id}`, {
        method: "PATCH",
        body: JSON.stringify({ topic, created_at: new Date(dateInput.value).toISOString() }),
      });
      if (!response.ok) { status.textContent = describeError(response); save.disabled = false; return; }
      close(); await loadDashboard();
    });
    remove.addEventListener("click", async () => {
      if (!window.confirm(`Delete the note “${entry.topic}”? This cannot be undone.`)) return;
      remove.disabled = true; status.textContent = "Deleting…";
      const response = await jsonFetch(`/api/v1/entries/${entry.id}`, { method: "DELETE" });
      if (!response.ok) { status.textContent = describeError(response); remove.disabled = false; return; }
      close(); await loadDashboard();
    });
    topicInput.focus();
  }

  // --- create-topic modal (SRA-902) ---------------------------------------

  const createTopicButton = document.querySelector('[data-action="create-topic"]');
  if (createTopicButton) {
    initCreateTopicModal(createTopicButton);
  }

  loadDashboard();

  function initCreateTopicModal(trigger) {
    let modal = null;
    let backdrop = null;

    trigger.addEventListener("click", () => {
      if (!modal) {
        const built = buildModal();
        modal = built.modal;
        backdrop = built.backdrop;
        document.body.appendChild(backdrop);
        bindModalHandlers(modal, backdrop, close);
      }
      resetModal(modal);
      backdrop.hidden = false;
      const firstInput = modal.querySelector("input, textarea");
      if (firstInput) firstInput.focus();
      document.addEventListener("keydown", escClose);
    });

    function escClose(event) {
      if (event.key === "Escape") {
        close();
        return;
      }
      if (event.key !== "Tab" || !modal || !backdrop || backdrop.hidden) return;

      const focusable = Array.from(modal.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href]'
      )).filter((node) => !node.hidden);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function close() {
      if (backdrop) backdrop.hidden = true;
      document.removeEventListener("keydown", escClose);
      trigger.focus();
    }
  }

  function bindModalHandlers(modal, backdrop, close) {
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) close();
    });
    const cancelBtn = modal.querySelector('[data-action="cancel"]');
    if (cancelBtn) cancelBtn.addEventListener("click", close);
    const closeBtn = modal.querySelector('[data-action="close-modal"]');
    if (closeBtn) closeBtn.addEventListener("click", close);
    const addUrlBtn = modal.querySelector('[data-action="add-url"]');
    const addPasteBtn = modal.querySelector('[data-action="add-paste"]');
    const addFileBtn = modal.querySelector('[data-action="add-file"]');
    const submitBtn = modal.querySelector('[data-action="submit"]');
    const form = modal.querySelector("form");
    if (addUrlBtn) addUrlBtn.addEventListener("click", () => addReferenceRow(modal, "url"));
    if (addPasteBtn) addPasteBtn.addEventListener("click", () => addReferenceRow(modal, "paste"));
    if (addFileBtn) addFileBtn.addEventListener("click", () => addReferenceRow(modal, "upload"));
    if (form) {
      form.addEventListener("submit", (event) => onSubmit(event, form, submitBtn, close));
    }
  }

  function buildModal() {
    const backdrop = el("div", {
      class: "modal-backdrop",
      id: "create-topic-modal",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": "create-topic-title",
      hidden: true,
    });

    const modal = el("div", { class: "modal" });
    const header = el("header", { class: "modal-header" });
    header.appendChild(el("h2", { id: "create-topic-title" }, "Create topic"));
    const closeBtn = el("button", {
      type: "button",
      class: "link-button",
      "data-action": "close-modal",
      "aria-label": "Close",
    }, "Close");
    header.appendChild(closeBtn);
    modal.appendChild(header);

    const form = el("form", { class: "modal-form", novalidate: true });

    const topicField = el("div", { class: "modal-field" });
    topicField.appendChild(el("label", { for: "ct-topic" }, "Topic"));
    topicField.appendChild(el("input", {
      type: "text", id: "ct-topic", name: "topic", required: true,
      maxlength: "200", placeholder: "e.g. Transformer attention heads",
    }));
    form.appendChild(topicField);

    const subtopicField = el("div", { class: "modal-field" });
    subtopicField.appendChild(el("label", { for: "ct-subtopic" }, "Subtopic (optional)"));
    subtopicField.appendChild(el("input", {
      type: "text", id: "ct-subtopic", name: "subtopic", maxlength: "200",
    }));
    form.appendChild(subtopicField);

    const tagsField = el("div", { class: "modal-field" });
    tagsField.appendChild(el("label", { for: "ct-tags" }, "Tags (comma-separated, optional)"));
    tagsField.appendChild(el("input", {
      type: "text", id: "ct-tags", name: "tags",
      placeholder: "deep-learning, transformers",
    }));
    form.appendChild(tagsField);

    const summaryField = el("div", { class: "modal-field" });
    summaryField.appendChild(el("label", { for: "ct-summary" }, "Summary (optional)"));
    summaryField.appendChild(el("textarea", {
      id: "ct-summary", name: "summary", rows: 3, maxlength: "2000",
    }));
    form.appendChild(summaryField);

    const refsHeader = el("div", { class: "modal-refs-header" });
    refsHeader.appendChild(el("h3", null, "References"));
    const addRow = el("div", { class: "modal-refs-actions" });
    addRow.appendChild(el("button", { type: "button", class: "button", "data-action": "add-url" }, "Add URL"));
    addRow.appendChild(el("button", { type: "button", class: "button", "data-action": "add-paste" }, "Add pasted text"));
    addRow.appendChild(el("button", { type: "button", class: "button", "data-action": "add-file" }, "Add file"));
    refsHeader.appendChild(addRow);
    form.appendChild(refsHeader);

    const refsList = el("div", { class: "modal-refs-list", id: "ct-refs-list" });
    form.appendChild(refsList);

    const status = el("p", {
      class: "modal-status status",
      id: "ct-status",
      role: "status",
      "aria-live": "polite",
    });
    form.appendChild(status);

    const actions = el("div", { class: "modal-actions" });
    actions.appendChild(el("button", { type: "button", class: "button", "data-action": "cancel" }, "Cancel"));
    actions.appendChild(el("button", { type: "submit", class: "button primary", "data-action": "submit" }, "Generate now"));
    form.appendChild(actions);

    modal.appendChild(form);
    backdrop.appendChild(modal);

    return { modal, backdrop };
  }

  function resetModal(modal) {
    const form = modal.querySelector("form");
    if (form) form.reset();
    const refsList = modal.querySelector("#ct-refs-list");
    if (refsList) refsList.replaceChildren();
    const status = modal.querySelector("#ct-status");
    if (status) {
      status.textContent = "";
      status.className = "modal-status status";
    }
    const submit = modal.querySelector('[data-action="submit"]');
    if (submit) {
      submit.disabled = false;
      submit.textContent = "Generate now";
    }
  }

  function addReferenceRow(modal, kind) {
    const refsList = modal.querySelector("#ct-refs-list");
    if (!refsList) return;
    const row = el("div", { class: "modal-ref-row", dataset: { kind: kind } });

    const header = el("div", { class: "modal-ref-row-header" });
    header.appendChild(el("span", { class: "modal-ref-kind" }, kindLabel(kind)));
    const removeBtn = el("button", {
      type: "button", class: "link-button", "data-action": "remove-ref",
    }, "Remove");
    header.appendChild(removeBtn);
    row.appendChild(header);

    const titleField = el("div", { class: "modal-field" });
    titleField.appendChild(el("label", null, "Title"));
    titleField.appendChild(el("input", { type: "text", name: "ref-title", required: true, maxlength: "200" }));
    row.appendChild(titleField);

    if (kind === "url") {
      const urlField = el("div", { class: "modal-field" });
      urlField.appendChild(el("label", null, "URL"));
      urlField.appendChild(el("input", { type: "url", name: "ref-url", required: true, placeholder: "https://…" }));
      row.appendChild(urlField);
    } else if (kind === "paste") {
      const pasteField = el("div", { class: "modal-field" });
      pasteField.appendChild(el("label", null, "Pasted text"));
      pasteField.appendChild(el("textarea", { name: "ref-pasted-text", rows: 4, required: true }));
      row.appendChild(pasteField);
    } else if (kind === "upload") {
      const fileField = el("div", { class: "modal-field" });
      fileField.appendChild(el("label", null, "File"));
      fileField.appendChild(el("input", {
        type: "file", name: "ref-file", required: true,
        accept: ".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown",
      }));
      const hiddenName = el("input", { type: "hidden", name: "ref-file-name" });
      const hiddenType = el("input", { type: "hidden", name: "ref-file-type" });
      const hiddenB64 = el("input", { type: "hidden", name: "ref-file-b64" });
      row.appendChild(fileField);
      row.appendChild(hiddenName);
      row.appendChild(hiddenType);
      row.appendChild(hiddenB64);
    }

    refsList.appendChild(row);
    removeBtn.addEventListener("click", () => row.remove());
  }

  function kindLabel(kind) {
    if (kind === "url") return "URL reference";
    if (kind === "paste") return "Pasted text reference";
    if (kind === "upload") return "Uploaded file reference";
    return kind;
  }

  async function onSubmit(event, form, submitBtn, close) {
    event.preventDefault();
    const refsList = form.querySelector("#ct-refs-list");
    const rows = refsList ? Array.from(refsList.querySelectorAll(".modal-ref-row")) : [];
    if (rows.length === 0) {
      setModalStatus(form, "Add at least one reference before generating.", "error");
      return;
    }

    const topic = form.querySelector("#ct-topic").value.trim();
    if (!topic) {
      setModalStatus(form, "Topic is required.", "error");
      return;
    }

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Generating…";
    }
    setModalStatus(form, "Reading files…", "");

    try {
      const references = [];
      for (const row of rows) {
        const kind = row.dataset.kind;
        const title = (row.querySelector('input[name="ref-title"]') || {}).value || "";
        const trimmedTitle = title.trim();
        if (!trimmedTitle) {
          setModalStatus(form, "Every reference needs a title.", "error");
          return;
        }
        if (kind === "url") {
          const url = (row.querySelector('input[name="ref-url"]') || {}).value || "";
          references.push({ kind: "url", title: trimmedTitle, url });
        } else if (kind === "paste") {
          const pasted = (row.querySelector('textarea[name="ref-pasted-text"]') || {}).value || "";
          references.push({ kind: "paste", title: trimmedTitle, pasted_text: pasted });
        } else if (kind === "upload") {
          const fileInput = row.querySelector('input[name="ref-file"]');
          const file = fileInput && fileInput.files && fileInput.files[0];
          if (!file) {
            setModalStatus(form, "Pick a file for every upload reference.", "error");
            return;
          }
          const b64 = await readFileAsDataURL(file).then((dataUrl) => stripDataUrlPrefix(dataUrl));
          references.push({
            kind: "upload",
            title: trimmedTitle,
            file_name: file.name,
            content_type: file.type || "application/octet-stream",
            file_b64: b64,
          });
        }
      }

      const subtopic = (form.querySelector("#ct-subtopic") || {}).value || "";
      const tagsRaw = (form.querySelector("#ct-tags") || {}).value || "";
      const summary = (form.querySelector("#ct-summary") || {}).value || "";
      const tags = tagsRaw.split(",").map((s) => s.trim()).filter((s) => s.length > 0);

      const body = { topic, references };
      if (subtopic.trim()) body.subtopic = subtopic.trim();
      if (tags.length > 0) body.tags = tags;
      if (summary.trim()) body.summary = summary.trim();

      setModalStatus(form, "Generating quiz and interview…", "");
      const res = await window.sraJsonFetch(TOPICS_API, {
        method: "POST",
        body: JSON.stringify(body),
      });

      if (res.status === 401) {
        window.location.href = "/signin?next=/dashboard";
        return;
      }
      if (!res.ok) {
        let message = "Could not create topic (" + res.status + ").";
        try {
          const detail = await res.json();
          if (detail && Array.isArray(detail)) {
            const first = detail[0];
            if (first && first.msg) message = "Could not create topic: " + first.msg;
          } else if (detail && detail.detail) {
            message = "Could not create topic: " +
              (typeof detail.detail === "string" ? detail.detail : JSON.stringify(detail.detail));
          }
        } catch (parseError) {
          // Fall back to the status code message.
        }
        setModalStatus(form, message, "error");
        return;
      }

      const result = await res.json();
      close();
      prependEntryCard(result.entry, result);
      await refreshReviewData();
    } catch (err) {
      setModalStatus(
        form,
        "Could not create topic: " + (err && err.message ? err.message : String(err)),
        "error"
      );
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Generate now";
      }
    }
  }

  function readFileAsDataURL(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("FileReader failed"));
      reader.readAsDataURL(file);
    });
  }

  function stripDataUrlPrefix(dataUrl) {
    const commaIndex = dataUrl.indexOf(",");
    if (commaIndex === -1) return dataUrl;
    return dataUrl.slice(commaIndex + 1);
  }

  function setModalStatus(form, message, kind) {
    const status = form.querySelector("#ct-status");
    if (!status) return;
    status.textContent = message;
    status.className = "modal-status status" + (kind ? " " + kind : "");
  }

  function prependEntryCard(entry, fullResult) {
    if (!entry) return;
    state.entries.unshift(entry);
    state.generationResults.set(String(entry.id), fullResult || {});
    rebuild();
  }

  async function refreshReviewData() {
    try {
      const [dueRes, upcomingRes] = await Promise.all([
        jsonFetch(DIGEST_API), jsonFetch(UPCOMING_API + "?days=365"),
      ]);
      if (dueRes.ok) state.due = await dueRes.json();
      if (upcomingRes.ok) state.upcoming = await upcomingRes.json();
      rebuild();
    } catch (err) {
      setStatus(upcomingStatus, "Review timing will refresh on your next visit.", "error");
    }
  }

  function buildEntryCard(entry, result) {
    const card = el("article", { class: "entry-card", dataset: { entryId: String(entry.id) } });
    const header = el("header", { class: "entry-card-header" });
    header.appendChild(el("h3", { class: "entry-topic" }, entry.topic));
    if (entry.subtopic) {
      header.appendChild(el("p", { class: "entry-subtopic muted" }, entry.subtopic));
    }
    card.appendChild(header);
    if (Array.isArray(entry.tags) && entry.tags.length > 0) {
      const tags = el("ul", { class: "entry-tags" });
      for (const tag of entry.tags) tags.appendChild(el("li", { class: "entry-tag" }, tag));
      card.appendChild(tags);
    }
    card.appendChild(el("p", { class: "entry-summary" }, entry.summary || ""));
    const meta = el("p", { class: "entry-meta muted" });
    meta.appendChild(document.createTextNode("Captured "));
    meta.appendChild(el("time", { datetime: entry.created_at }, entry.created_at));
    card.appendChild(meta);

    const actions = el("p", { class: "entry-actions" });
    const quizSetId = result && (result.quiz_set_id || (result.quiz_set && result.quiz_set.id));
    const interviewPlanId = result && (result.interview_plan_id || (result.interview_plan && result.interview_plan.id));
    if (quizSetId) {
      actions.appendChild(el("a", { class: "button", href: "/quiz" }, "Start quiz"));
    } else {
      actions.appendChild(el("span", { class: "muted" }, "Quiz will appear once generation lands."));
    }
    if (quizSetId && interviewPlanId) {
      actions.appendChild(el("a", {
        class: "button",
        href: "/dashboard/interview?quiz_set_id=" + quizSetId,
      }, "Start interview"));
    } else {
      actions.appendChild(el("span", { class: "muted" }, "Interview will appear once generation lands."));
    }
    const regenBtn = el("button", { type: "button", class: "button", dataset: { action: "regenerate" } }, "Regenerate");
    regenBtn.addEventListener("click", () => onRegenerate(card, entry.id, regenBtn));
    actions.appendChild(regenBtn);
    card.appendChild(actions);

    if (Array.isArray(result && result.errors) && result.errors.length > 0) {
      const errs = el("ul", { class: "entry-errors muted" });
      for (const err of result.errors) errs.appendChild(el("li", null, err));
      card.appendChild(errs);
    }
    return card;
  }

  async function onRegenerate(card, entryId, button) {
    if (!card || !entryId) return;
    button.disabled = true;
    const previousLabel = button.textContent;
    button.textContent = "Regenerating…";
    try {
      const res = await window.sraJsonFetch(TOPICS_API + "/" + entryId + "/regenerate", {
        method: "POST",
      });
      if (!res.ok) {
        card.appendChild(el(
          "p",
          { class: "error entry-regen-note" },
          "Regenerate failed (" + res.status + ")."
        ));
        return;
      }
      const result = await res.json();
      const actions = card.querySelector(".entry-actions");
      if (actions && result) {
        while (actions.firstChild) actions.removeChild(actions.firstChild);
        const quizSetId = result.quiz_set_id;
        const interviewPlanId = result.interview_plan_id;
        if (quizSetId) {
          actions.appendChild(el("a", { class: "button", href: "/quiz" }, "Start quiz"));
        } else {
          actions.appendChild(el("span", { class: "muted" }, "Quiz generation failed."));
        }
        if (quizSetId && interviewPlanId) {
          actions.appendChild(el("a", {
            class: "button",
            href: "/dashboard/interview?quiz_set_id=" + quizSetId,
          }, "Start interview"));
        } else {
          actions.appendChild(el("span", { class: "muted" }, "Interview generation failed."));
        }
        actions.appendChild(button);
      }
      if (Array.isArray(result.generation_errors) && result.generation_errors.length > 0) {
        const note = el("p", { class: "error entry-regen-note" }, result.generation_errors.join(" "));
        card.appendChild(note);
      }
    } catch (err) {
      card.appendChild(el(
        "p",
        { class: "error entry-regen-note" },
        "Regenerate failed: " + (err && err.message ? err.message : String(err))
      ));
    } finally {
      button.disabled = false;
      button.textContent = previousLabel;
    }
  }
})();
