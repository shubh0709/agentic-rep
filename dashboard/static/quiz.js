// dashboard/static/quiz.js
// SRA-405: Browser entry point for the quiz dashboard.
// Plain ES module, no build tools, no dependencies.
// Uses `globalThis.fetch` only. Operates on the DOM via the `doc`
// parameter passed to `initQuiz(root, doc)` — never reads the global
// `document` directly.

(function () {
  "use strict";

  var API_BASE = "/api/v1/quiz";

  function el(doc, tag, attrs, text) {
    var node = doc.createElement(tag);
    if (attrs) {
      var keys = Object.keys(attrs);
      for (var i = 0; i < keys.length; i++) {
        var key = keys[i];
        node.setAttribute(key, String(attrs[key]));
      }
    }
    if (text != null) {
      node.appendChild(doc.createTextNode(String(text)));
    }
    return node;
  }

  function clearChildren(node) {
    if (!node) return;
    if (Array.isArray(node.children)) {
      while (node.children.length > 0) node.children.pop();
    }
    if ("textContent" in node) node.textContent = "";
  }

  function statusPanel(doc, root, status, title, detail) {
    clearChildren(root);
    var section = el(doc, "section", {
      "data-panel": "status",
      "data-status": String(status),
      role: "status",
      "aria-live": "polite",
    });
    section.appendChild(el(doc, "h2", null, title));
    section.appendChild(
      el(doc, "p", null, "HTTP " + status + " \u2014 " + detail)
    );
    root.appendChild(section);
  }

  function statusTitle(status) {
    if (status === 401) return "Not authenticated (401)";
    if (status === 403) return "Forbidden (403)";
    if (status === 404) return "Not found (404)";
    if (status === 429) return "Rate limit exceeded (429)";
    return "Request failed (" + status + ")";
  }

  function friendlyDetail(status, retryAfter) {
    if (status === 401) {
      return "Please sign in to view today's quizzes.";
    }
    if (status === 403) {
      return "You do not have access to this quiz.";
    }
    if (status === 404) {
      return "No quizzes are available or this resource was not found.";
    }
    if (status === 429) {
      if (retryAfter) {
        return "Too many requests. Retry after " + retryAfter + " seconds.";
      }
      return "Too many requests. Please slow down and try again shortly.";
    }
    return "The server returned an unexpected status.";
  }

  function handleErrorStatus(doc, root, res) {
    if (!res) return false;
    var status = res.status;
    if (
      status === 401 ||
      status === 403 ||
      status === 404 ||
      status === 429
    ) {
      var retry = null;
      if (
        status === 429 &&
        res.headers &&
        typeof res.headers.get === "function"
      ) {
        retry = res.headers.get("retry-after") || res.headers.get("Retry-After");
      }
      statusPanel(
        doc,
        root,
        status,
        statusTitle(status),
        friendlyDetail(status, retry)
      );
      return true;
    }
    return false;
  }

  function renderTodayList(doc, root, items) {
    clearChildren(root);
    var count = Array.isArray(items) ? items.length : 0;
    root.appendChild(
      el(doc, "h2", null, "Today's quizzes (" + count + ")")
    );
    if (count === 0) {
      root.appendChild(
        el(doc, "p", null, "No quizzes are ready yet. Generate one from the study agent.")
      );
      return;
    }
    var list = el(doc, "ul", { "data-panel": "today-list" });
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      var li = el(doc, "li", {
        "data-quiz-set-id": String(item.id),
        "data-panel": "today-item",
      });
      var entry = item.entry || {};
      var topic = entry.topic || "Untitled";
      var qCount = Array.isArray(item.questions)
        ? item.questions.length
        : 0;
      li.appendChild(el(doc, "span", { "data-field": "topic" }, topic));
      li.appendChild(
        el(doc, "span", { "data-field": "count" }, qCount + " questions")
      );
      var actions = el(doc, "span", { "data-field": "actions" });
      actions.appendChild(
        el(doc, "button", {
          "data-action": "start",
          "data-quiz-set-id": String(item.id),
          type: "button",
        }, "Start")
      );
      actions.appendChild(
        el(doc, "button", {
          "data-action": "start-interview",
          "data-quiz-set-id": String(item.id),
          type: "button",
        }, "Start interview")
      );
      li.appendChild(actions);
      list.appendChild(li);
    }
    root.appendChild(list);
  }

  function readBody(res) {
    if (!res || typeof res.json !== "function") return Promise.resolve(null);
    return res.json();
  }

  function loadTodayQuizzesImpl(state) {
    var fetchImpl = state.fetchImpl;
    var doc = state.doc;
    var root = state.root;
    var url = API_BASE + "/today";
    return fetchImpl(url, { credentials: "same-origin" }).then(function (res) {
      if (handleErrorStatus(doc, root, res)) return [];
      if (!res.ok) {
        statusPanel(
          doc,
          root,
          res.status,
          statusTitle(res.status || 500),
          friendlyDetail(res.status || 500, null)
        );
        return [];
      }
      return readBody(res).then(function (data) {
        state.todayList = Array.isArray(data) ? data : [];
        renderTodayList(doc, root, state.todayList);
        return state.todayList;
      });
    });
  }

  function startQuizImpl(state, quizSetId) {
    var fetchImpl = state.fetchImpl;
    var doc = state.doc;
    var root = state.root;
    var url = API_BASE + "/" + encodeURIComponent(quizSetId) + "/attempts";
    return fetchImpl(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode: "web" }),
    }).then(function (res) {
      if (handleErrorStatus(doc, root, res)) return null;
      if (res.status === 400) {
        return readBody(res).then(function (body) {
          var detail =
            (body && (body.detail || body.message)) ||
            "Quiz cannot be started in the requested mode.";
          statusPanel(doc, root, 400, "Cannot start quiz (400)", detail);
          return null;
        });
      }
      if (!res.ok) {
        statusPanel(
          doc,
          root,
          res.status,
          statusTitle(res.status || 500),
          friendlyDetail(res.status || 500, null)
        );
        return null;
      }
      return readBody(res).then(function (body) {
        body = body || {};
        state.attemptId = body.attempt_id != null ? body.attempt_id : null;
        state.quizSetId = quizSetId;
        return body;
      });
    });
  }

  function loadQuizDetailImpl(state, quizSetId) {
    var fetchImpl = state.fetchImpl;
    var doc = state.doc;
    var root = state.root;
    var url = API_BASE + "/" + encodeURIComponent(quizSetId);
    return fetchImpl(url, { credentials: "same-origin" }).then(function (res) {
      if (handleErrorStatus(doc, root, res)) return null;
      if (!res.ok) {
        statusPanel(
          doc,
          root,
          res.status,
          statusTitle(res.status || 500),
          friendlyDetail(res.status || 500, null)
        );
        return null;
      }
      return readBody(res).then(function (body) {
        body = body || {};
        state.quizDetail = body;
        state.questions = Array.isArray(body.questions) ? body.questions : [];
        return body;
      });
    });
  }

  function submitAnswerImpl(state, attemptId, questionId, selectedOptionIndex) {
    var fetchImpl = state.fetchImpl;
    var doc = state.doc;
    var root = state.root;
    var url = API_BASE + "/attempts/" + encodeURIComponent(attemptId) + "/answers";
    return fetchImpl(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        question_id: questionId,
        selected_option_index: selectedOptionIndex,
      }),
    }).then(function (res) {
      if (handleErrorStatus(doc, root, res)) return null;
      if (res.status === 400 || res.status === 404 || res.status === 409) {
        return readBody(res).then(function (body) {
          var detail =
            (body && (body.detail || body.message)) ||
            "The answer was not accepted by the server.";
          statusPanel(doc, root, res.status, statusTitle(res.status), detail);
          return null;
        });
      }
      if (!res.ok) {
        statusPanel(
          doc,
          root,
          res.status,
          statusTitle(res.status || 500),
          friendlyDetail(res.status || 500, null)
        );
        return null;
      }
      return readBody(res).then(function (body) {
        return body || null;
      });
    });
  }

  function completeAttemptImpl(state, attemptId) {
    var fetchImpl = state.fetchImpl;
    var doc = state.doc;
    var root = state.root;
    var url =
      API_BASE + "/attempts/" + encodeURIComponent(attemptId) + "/complete";
    return fetchImpl(url, {
      method: "POST",
      credentials: "same-origin",
    }).then(function (res) {
      if (handleErrorStatus(doc, root, res)) return null;
      if (res.status === 400 || res.status === 409) {
        return readBody(res).then(function (body) {
          var detail =
            (body && (body.detail || body.message)) ||
            "The attempt could not be completed.";
          statusPanel(doc, root, res.status, statusTitle(res.status), detail);
          return null;
        });
      }
      if (!res.ok) {
        statusPanel(
          doc,
          root,
          res.status,
          statusTitle(res.status || 500),
          friendlyDetail(res.status || 500, null)
        );
        return null;
      }
      return readBody(res).then(function (body) {
        body = body || {};
        state.finalScore = body.score != null ? body.score : null;
        state.finalTotal = body.total_questions != null ? body.total_questions : null;
        return body;
      });
    });
  }

  function initQuiz(root, doc) {
    if (!root) throw new Error("initQuiz: root element is required");
    if (!doc) throw new Error("initQuiz: doc is required");
    if (
      typeof doc.createElement !== "function" ||
      typeof doc.createTextNode !== "function"
    ) {
      throw new Error("initQuiz: doc must expose createElement and createTextNode");
    }
    var fetchImpl = null;
    if (typeof globalThis !== "undefined" && typeof globalThis.fetch === "function") {
      fetchImpl = globalThis.fetch.bind(globalThis);
    } else if (typeof fetch === "function") {
      fetchImpl = fetch;
    }
    if (!fetchImpl) {
      throw new Error("initQuiz: globalThis.fetch is not available");
    }

    var state = {
      root: root,
      doc: doc,
      fetchImpl: fetchImpl,
      quizSetId: null,
      attemptId: null,
      todayList: [],
      quizDetail: null,
      questions: [],
      answers: [],
      finalScore: null,
      finalTotal: null,
    };

    clearChildren(root);
    root.appendChild(
      el(doc, "p", { "data-panel": "initial" }, "Loading today's quizzes\u2026")
    );

    var sraQuiz = {
      initQuiz: initQuiz,
      loadTodayQuizzes: function () { return loadTodayQuizzesImpl(state); },
      startQuiz: function (id) { return startQuizImpl(state, id); },
      loadQuizDetail: function (id) { return loadQuizDetailImpl(state, id); },
      submitAnswer: function (attemptId, questionId, idx) {
        return submitAnswerImpl(state, attemptId, questionId, idx);
      },
      completeAttempt: function (attemptId) {
        return completeAttemptImpl(state, attemptId);
      },
      state: state,
    };

    try {
      if (typeof globalThis !== "undefined") {
        globalThis.sraQuiz = sraQuiz;
        globalThis.initQuiz = initQuiz;
        globalThis.loadTodayQuizzes = sraQuiz.loadTodayQuizzes;
        globalThis.startQuiz = sraQuiz.startQuiz;
        globalThis.submitAnswer = sraQuiz.submitAnswer;
        globalThis.completeAttempt = sraQuiz.completeAttempt;
      }
      if (typeof window !== "undefined") {
        window.sraQuiz = sraQuiz;
      }
    } catch (e) {
      // non-browser host: skip global exposure
    }

    sraQuiz.loadTodayQuizzes();

    return sraQuiz;
  }

  // Expose as a global function so the unit-test driver (which loads the
  // source via `new Function`) and the dashboard's HTML shell can both
  // reach `initQuiz` without needing an `export` statement.
  if (typeof globalThis !== "undefined") {
    globalThis.initQuiz = initQuiz;
  }
  if (typeof window !== "undefined") {
    window.initQuiz = initQuiz;
  }
})();
