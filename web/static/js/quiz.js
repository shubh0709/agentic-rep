// web/static/js/quiz.js
// Vanilla ES module for the quiz page. No build tools, no frameworks.
// All requests use credentials: "same-origin" so the session cookie
// issued by /api/v1/auth/google/callback is included automatically.

(function () {
  "use strict";

  const API = "/api/v1/quiz";

  const root = document.getElementById("quiz-root");
  const status = document.getElementById("quiz-status");
  if (!root) return;

  function setStatus(text, kind) {
    if (!status) return;
    status.textContent = text;
    status.className = "lede" + (kind ? " " + kind : " muted");
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
    const node = el("section", { class: "panel " + kind, ...attrs });
    return node;
  }

  function describeError(res) {
    if (res.status === 401) return "Your session expired. Please sign in again.";
    if (res.status === 403) return "You don't have access to this quiz.";
    if (res.status === 404) return "That quiz or question was not found.";
    if (res.status === 409) return "This attempt has already been completed.";
    if (res.status === 429) return "Too many requests. Please slow down.";
    return `The server returned ${res.status}.`;
  }

  async function jsonFetch(url, init) {
    return window.sraJsonFetch(url, init);
  }

  function renderEmpty() {
    setStatus("No quizzes scheduled for today.");
    root.replaceChildren(
      panel("empty", { dataset: { panel: "status" } }, )
    );
    root.firstChild.appendChild(
      el("p", null, "New quizzes are generated automatically each morning. Check back later, or add new material to your study queue.")
    );
  }

  async function renderTodayList() {
    setStatus("Loading today's quizzes…");
    const res = await jsonFetch(API + "/today");
    if (res.status === 401) {
      window.location.href = "/signin?next=/quiz";
      return;
    }
    if (!res.ok) {
      setStatus(describeError(res), "error");
      return;
    }
    const sets = await res.json();
    if (!Array.isArray(sets) || sets.length === 0) {
      renderEmpty();
      return;
    }
    setStatus(`Pick a quiz to start (${sets.length} available today).`);
    const list = panel("today-list", { dataset: { panel: "today-list" } });
    const ul = el("ul");
    for (const quizSet of sets) {
      const li = el("li");
      const topic = quizSet.entry?.topic || "Untitled entry";
      const sub = quizSet.entry?.subtopic ? ` — ${quizSet.entry.subtopic}` : "";
      li.appendChild(el("div", null, topic + sub));
      const button = el("button", { class: "button primary", type: "button" }, "Start");
      button.addEventListener("click", () => startAttempt(quizSet));
      li.appendChild(button);
      ul.appendChild(li);
    }
    list.appendChild(ul);
    root.replaceChildren(list);
  }

  async function startAttempt(quizSet) {
    setStatus(`Starting quiz on ${quizSet.entry?.topic || "entry"}…`);
    const startRes = await jsonFetch(`${API}/${quizSet.id}/attempts`, {
      method: "POST",
      body: JSON.stringify({ mode: "web" }),
    });
    if (!startRes.ok) {
      setStatus(describeError(startRes), "error");
      return;
    }
    const attempt = await startRes.json();
    const quizRes = await jsonFetch(`${API}/${quizSet.id}`);
    if (!quizRes.ok) {
      setStatus(describeError(quizRes), "error");
      return;
    }
    const fullSet = await quizRes.json();
    runQuiz(quizSet, attempt.attempt_id, fullSet.questions);
  }

  function runQuiz(quizSet, attemptId, questions) {
    let index = 0;
    let correctCount = 0;
    setStatus(`Quiz on ${quizSet.entry?.topic || "entry"}`);

    function renderQuestion() {
      const q = questions[index];
      const wrap = panel("question");
      wrap.appendChild(el("h2", { class: "question-text" }, `Question ${index + 1} of ${questions.length}`));
      wrap.appendChild(el("p", { class: "question-text" }, q.question_text));
      const options = el("ol", { class: "options" });
      q.options.forEach((text, i) => {
        const btn = el("button", { type: "button" }, `${String.fromCharCode(65 + i)}.  ${text}`);
        btn.dataset.optionIndex = String(i);
        btn.addEventListener("click", () => onAnswer(btn, i));
        const li = el("li");
        li.appendChild(btn);
        options.appendChild(li);
      });
      wrap.appendChild(options);
      root.replaceChildren(wrap);
    }

    async function onAnswer(button, selected) {
      const buttons = root.querySelectorAll(".options button");
      buttons.forEach((b) => (b.disabled = true));
      const res = await jsonFetch(`${API}/attempts/${attemptId}/answers`, {
        method: "POST",
        body: JSON.stringify({ question_id: questions[index].id, selected_option_index: selected }),
      });
      if (!res.ok) {
        buttons.forEach((b) => (b.disabled = false));
        setStatus(describeError(res), "error");
        return;
      }
      const result = await res.json();
      if (result.is_correct) correctCount++;
      buttons.forEach((b, i) => {
        if (i === result.correct_option_index) b.classList.add("correct");
        else if (i === selected) b.classList.add("incorrect");
      });
      if (result.explanation) {
        const exp = el("p", { class: "explanation" }, result.explanation);
        root.firstChild.appendChild(exp);
      }
      const next = el("button", { class: "button primary", type: "button" }, index + 1 === questions.length ? "Finish" : "Next question");
      next.addEventListener("click", advance);
      root.firstChild.appendChild(next);
    }

    async function advance() {
      index++;
      if (index >= questions.length) {
        await complete();
        return;
      }
      renderQuestion();
    }

    async function complete() {
      const res = await jsonFetch(`${API}/attempts/${attemptId}/complete`, { method: "POST" });
      if (!res.ok) {
        setStatus(describeError(res), "error");
        return;
      }
      const result = await res.json();
      setStatus("Quiz complete.");
      const summary = panel("summary");
      summary.appendChild(el("p", { class: "summary" }, `Score: ${result.score} / ${result.total_questions}`));
      const back = el("a", { class: "button", href: "/quiz" }, "Back to today's quizzes");
      summary.appendChild(back);
      root.replaceChildren(summary);
    }

    renderQuestion();
  }

  renderTodayList().catch((err) => {
    setStatus("Network error: " + (err && err.message ? err.message : String(err)), "error");
  });
})();