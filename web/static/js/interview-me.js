// SRA-1101 states: sources -> topics -> configure -> mcq -> interview -> results.
(function () {
  "use strict";
  const API = "/api/v1/assessments";
  const ANALYZE_API = "/api/v1/assessments/analyze";
  const root = document.getElementById("interview-me-root");
  if (!root) return;
  const state = { assessment: null, config: null, quiz: null, questionIndex: 0, mcqAttemptId: null, interviewAttemptId: null };

  function el(tag, attrs, text) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === "class") node.className = value;
      else node.setAttribute(key, value);
    }
    if (text != null) node.textContent = text;
    return node;
  }
  function setState(name) { root.dataset.state = name; root.replaceChildren(); }
  function status(message, kind) { root.prepend(el("p", { class: "status " + (kind || "") }, message)); }
  async function request(url, options) {
    const response = await window.sraJsonFetch(url, options || {});
    if (!response.ok) {
      let detail = "Request failed (" + response.status + ").";
      try { const body = await response.json(); detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body); } catch (_) {}
      throw new Error(detail);
    }
    return response.json();
  }
  function fileBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || "").split(",").pop());
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  function renderSources() {
    setState("sources");
    const workspace = el("div", { class: "assessment-source-layout" });
    const formCard = el("section", { class: "assessment-source-card" });
    formCard.append(
      el("p", { class: "assessment-step" }, "Step 1 of 3"),
      el("h2", {}, "Add your material"),
      el("p", { class: "muted assessment-intro" }, "Give your session a topic, then add notes, a link, or a document."),
    );
    const form = el("form", { class: "stacked-form assessment-form" });
    form.innerHTML = `
      <label for="assessment-topic">Topic</label>
      <input id="assessment-topic" name="topic" required maxlength="200" placeholder="e.g. Transformer attention">
      <label for="assessment-notes">Paste notes <span class="optional">optional</span></label>
      <textarea id="assessment-notes" name="paste" rows="7" placeholder="Paste the material you want to practise"></textarea>
      <div class="assessment-divider"><span>or add a source</span></div>
      <label for="assessment-url">Reference URL <span class="optional">optional</span></label>
      <input id="assessment-url" name="url" type="url" placeholder="https://example.com/article">
      <label class="assessment-file-field" for="assessment-file">
        <strong>Upload a document</strong>
        <span>PDF, DOCX, TXT, or Markdown</span>
      </label>
      <input id="assessment-file" name="file" type="file" accept=".pdf,.docx,.txt,.md">
      <button class="button primary assessment-submit" type="submit">Analyze material <span aria-hidden="true">→</span></button>`;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const topic = form.topic.value.trim();
      const references = [];
      if (form.paste.value.trim()) references.push({ kind: "paste", title: "Pasted notes", pasted_text: form.paste.value.trim() });
      if (form.url.value.trim()) references.push({ kind: "url", title: "Web reference", url: form.url.value.trim() });
      const file = form.file.files && form.file.files[0];
      if (file) references.push({ kind: "upload", title: file.name, file_name: file.name, content_type: file.type || "text/plain", file_b64: await fileBase64(file) });
      if (!references.length) { status("Add pasted text, a URL, or a supported file.", "error"); return; }
      form.querySelector("button").disabled = true;
      try {
        state.assessment = await request(ANALYZE_API, { method: "POST", body: JSON.stringify({ topic, references }) });
        renderTopics();
      } catch (error) { status(error.message, "error"); form.querySelector("button").disabled = false; }
    });
    formCard.append(form);
    workspace.append(formCard);
    const history = el("section", { class: "assessment-history", "aria-labelledby": "assessment-history-title" });
    history.append(el("h2", { id: "assessment-history-title" }, "Recent assessments"), el("p", { class: "muted" }, "Loading saved sessions…"));
    workspace.append(history);
    root.append(workspace);
    request(API).then((rows) => {
      history.replaceChildren(el("h2", { id: "assessment-history-title" }, "Recent assessments"));
      if (!rows.length) { history.append(el("p", { class: "muted" }, "No saved assessments yet.")); return; }
      const list = el("ul", { class: "entries-list" });
      rows.forEach((row) => {
        const item = el("li", { class: "entry-card" });
        item.append(el("strong", {}, row.topic), el("span", { class: "muted" }, " · " + row.status + (row.mode ? " · " + row.mode : "")));
        const open = el("button", { class: "button", type: "button" }, row.status === "completed" ? "View results" : "Resume");
        open.addEventListener("click", () => resumeAssessment(row.id));
        item.append(open); list.append(item);
      });
      history.append(list);
    }).catch(() => history.replaceChildren(el("h2", { id: "assessment-history-title" }, "Recent assessments"), el("p", { class: "muted" }, "History is temporarily unavailable.")));
  }

  async function resumeAssessment(id) {
    try {
      state.assessment = await request(API + "/" + id);
      history.replaceState(null, "", "/interview-me?assessment_id=" + id);
      if (state.assessment.status === "completed") { await renderResults(); return; }
      if (state.assessment.status === "draft") { renderTopics(); return; }
      state.config = {
        mode: state.assessment.mode,
        mcq_question_count: state.assessment.mcq_question_count,
        interview_question_count: state.assessment.interview_question_count,
        selected_topic_keys: state.assessment.selected_topic_keys,
      };
      if (state.assessment.interview_attempt_id) {
        state.interviewAttemptId = state.assessment.interview_attempt_id;
        const detail = await request("/api/v1/quiz/attempts/" + state.interviewAttemptId + "/interview");
        if (detail.attempt.completed_at) { await renderResults(); return; }
        const questions = detail.turns.filter((turn) => turn.role === "ai" && turn.score == null);
        renderInterview(questions.length ? questions[questions.length - 1].content : "Continue the interview.");
        return;
      }
      if (state.assessment.mcq_attempt_id) {
        if (state.assessment.mcq_completed && state.assessment.mode === "both") {
          await startInterview();
          return;
        }
        const started = await request(API + "/" + state.assessment.id + "/start/mcq", { method: "POST" });
        state.mcqAttemptId = started.attempt_id;
        state.quiz = await request("/api/v1/quiz/" + state.assessment.quiz_set_id);
        state.questionIndex = Number(state.assessment.mcq_answered_count || 0);
        if (state.questionIndex < state.quiz.questions.length) renderMcqQuestion();
        else await renderResults();
        return;
      }
      if (state.assessment.mode === "interview") await startInterview(); else await startMcq();
    } catch (error) { setState("sources"); status(error.message, "error"); }
  }

  function renderTopics() {
    setState("topics");
    root.append(el("h2", {}, "Choose exact topics"));
    const form = el("form", { class: "assessment-topics" });
    for (const item of state.assessment.suggestions) {
      const label = el("label", { class: "assessment-topic" });
      const checkbox = el("input", { type: "checkbox", name: "selected_topic_keys", value: item.key, checked: "checked" });
      label.append(checkbox, el("strong", {}, item.label), el("span", {}, item.description), el("small", {}, "Source: “" + item.evidence_excerpt + "”"));
      form.append(label);
    }
    form.append(el("button", { class: "button primary", type: "submit" }, "Configure assessment"));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const selected = Array.from(form.querySelectorAll("input:checked")).map((node) => node.value);
      if (!selected.length) { status("Select at least one topic.", "error"); return; }
      state.selected_topic_keys = selected;
      renderConfigure();
    });
    root.append(form);
  }

  function renderConfigure() {
    setState("configure");
    root.append(el("h2", {}, "Configure the session"));
    const form = el("form", { class: "stacked-form assessment-form" });
    form.innerHTML = `
      <label>Mode<select name="mode"><option value="interview">AI interview</option><option value="mcq">MCQ</option><option value="both" selected>Both (MCQ first)</option></select></label>
      <label>MCQ questions<input name="mcq_question_count" type="number" min="1" max="20" value="5"></label>
      <label>Interview questions<input name="interview_question_count" type="number" min="3" max="12" value="3"></label>
      <button class="button primary" type="submit">Generate and start</button>`;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      state.config = { selected_topic_keys: state.selected_topic_keys, mode: form.mode.value, mcq_question_count: Number(form.mcq_question_count.value), interview_question_count: Number(form.interview_question_count.value) };
      try {
        state.assessment = await request(API + "/" + state.assessment.id + "/generate", { method: "POST", body: JSON.stringify(state.config) });
        if (state.config.mode === "interview") await startInterview(); else await startMcq();
      } catch (error) { status(error.message, "error"); }
    });
    root.append(form);
  }

  async function startMcq() {
    setState("mcq"); status("Loading MCQ…");
    const started = await request(API + "/" + state.assessment.id + "/start/mcq", { method: "POST" });
    state.mcqAttemptId = started.attempt_id;
    state.quiz = await request("/api/v1/quiz/" + state.assessment.quiz_set_id);
    state.questionIndex = 0; renderMcqQuestion();
  }
  function renderMcqQuestion() {
    setState("mcq");
    const question = state.quiz.questions[state.questionIndex];
    root.append(el("p", { class: "muted" }, "Question " + (state.questionIndex + 1) + " of " + state.quiz.questions.length));
    root.append(el("h2", {}, question.question_text));
    const options = el("div", { class: "assessment-options" });
    question.options.forEach((option, index) => {
      const button = el("button", { class: "button", type: "button" }, option);
      button.addEventListener("click", async () => {
        await request("/api/v1/quiz/attempts/" + state.mcqAttemptId + "/answers", { method: "POST", body: JSON.stringify({ question_id: question.id, selected_option_index: index }) });
        state.questionIndex += 1;
        if (state.questionIndex < state.quiz.questions.length) renderMcqQuestion();
        else {
          await request("/api/v1/quiz/attempts/" + state.mcqAttemptId + "/complete", { method: "POST" });
          if (state.config.mode === "both") await startInterview(); else await renderResults();
        }
      });
      options.append(button);
    });
    root.append(options);
  }

  async function startInterview() {
    setState("interview"); status("Starting AI interview…");
    const started = await request(API + "/" + state.assessment.id + "/start/interview", { method: "POST" });
    state.interviewAttemptId = started.attempt_id;
    renderInterview(started.question_text || "Answer the current interview question.");
  }
  function renderInterview(question) {
    setState("interview");
    root.append(el("h2", {}, question));
    const form = el("form", { class: "stacked-form assessment-form" });
    const answer = el("textarea", { rows: "6", required: "required", placeholder: "Type your answer" });
    form.append(answer, el("button", { class: "button primary", type: "submit" }, "Send answer"));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const turn = await request("/api/v1/quiz/attempts/" + state.interviewAttemptId + "/interview-turn", { method: "POST", body: JSON.stringify({ action: "answer", answer_text: answer.value }) });
        if (turn.finished) await renderResults(); else renderInterview(turn.question_text);
      } catch (error) { status(error.message, "error"); }
    });
    root.append(form);
  }

  async function renderResults() {
    setState("results");
    try {
      const report = await request(API + "/" + state.assessment.id + "/results");
      window.dispatchEvent(new CustomEvent("study:completed", {
        detail: { kind: "assessment" },
      }));
      root.append(el("h2", {}, "Assessment results"), el("p", { class: "summary" }, "Overall score: " + report.overall_score));
      for (const title of ["strengths", "weaknesses", "deep_dive_topics"]) {
        root.append(el("h3", {}, title.replaceAll("_", " ")));
        const list = el("ul"); (report[title] || []).forEach((value) => list.append(el("li", {}, value))); root.append(list);
      }
      const details = el("div", { class: "assessment-results" });
      for (const item of report.items || []) details.append(el("article", { class: "panel" }, item.question + "\nYour answer: " + item.answer + "\nFeedback: " + item.feedback + "\nWhy: " + item.rationale));
      root.append(details, el("button", { class: "button", type: "button", "data-action": "new" }, "Start another"));
      root.querySelector('[data-action="new"]').addEventListener("click", renderSources);
    } catch (error) { status(error.message, "error"); }
  }

  const requestedId = new URLSearchParams(window.location.search).get("assessment_id");
  if (requestedId) resumeAssessment(requestedId); else renderSources();
})();
