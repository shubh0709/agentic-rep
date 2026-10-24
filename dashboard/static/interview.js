// dashboard/static/interview.js
// SRA-807: Browser entry point for the interview dashboard.
// Plain ES module, no build tools, no dependencies.
// Uses `globalThis.fetch` only. Operates on the DOM via the `doc`
// parameter passed to `initInterview(root, doc)` — never reads the global
// `document` directly. Mirrors the helper style of `dashboard/static/quiz.js`.

(function () {
  "use strict";

  var API_BASE = "/api/v1/quiz";

  // `appendChild` shim: real DOM nodes expose `appendChild`; the
  // unit-test driver's fake root only exposes a `children` array. The
  // helper picks the right shape so the same code works in the browser
  // and in the Node-driven harness.
  function appendChild(parent, child) {
    if (!parent || !child) return;
    if (typeof parent.appendChild === "function") {
      parent.appendChild(child);
    } else if (Array.isArray(parent.children)) {
      parent.children.push(child);
      child.parent = parent;
    }
  }

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
      appendChild(node, doc.createTextNode(String(text)));
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
    appendChild(section, el(doc, "h2", null, title));
    appendChild(
      section,
      el(doc, "p", null, "HTTP " + status + " \u2014 " + detail)
    );
    appendChild(root, section);
  }

  function statusTitle(status) {
    if (status === 400) return "Cannot start interview (400)";
    if (status === 401) return "Not authenticated (401)";
    if (status === 403) return "Forbidden (403)";
    if (status === 404) return "Not found (404)";
    if (status === 409) return "Conflict (409)";
    if (status === 422) return "Invalid response (422)";
    if (status === 429) return "Rate limit exceeded (429)";
    return "Request failed (" + status + ")";
  }

  function friendlyDetail(status, retryAfter) {
    if (status === 400) {
      return "Interview mode is not available for this quiz.";
    }
    if (status === 401) {
      return "Please sign in to start an interview.";
    }
    if (status === 403) {
      return "You do not have access to this interview.";
    }
    if (status === 404) {
      return "The interview attempt was not found.";
    }
    if (status === 409) {
      return "This interview attempt is already finished.";
    }
    if (status === 422) {
      return "The interview service returned an invalid response.";
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
      status === 400 ||
      status === 401 ||
      status === 403 ||
      status === 404 ||
      status === 409 ||
      status === 422 ||
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

  function renderThread(doc, root, turns, finished, nextQuestion) {
    clearChildren(root);
    var thread = el(doc, "div", { "data-panel": "thread" });
    var list = el(doc, "ol", { "data-panel": "transcript" });
    var arr = Array.isArray(turns) ? turns : [];
    for (var i = 0; i < arr.length; i++) {
      var turn = arr[i] || {};
      var role = String(turn.role || "ai");
      var bubble = el(doc, "li", {
        "data-panel": "turn",
        "data-role": role,
      });
      appendChild(bubble, el(doc, "span", { "data-field": "role" }, role));
      appendChild(
        bubble,
        el(doc, "span", { "data-field": "content" }, String(turn.content || ""))
      );
      if (typeof turn.score === "number") {
        appendChild(
          bubble,
          el(doc, "span", { "data-field": "score" }, String(turn.score))
        );
      }
      if (turn.outline_index != null) {
        appendChild(
          bubble,
          el(doc, "span", { "data-field": "outline-index" }, String(turn.outline_index))
        );
      }
      appendChild(list, bubble);
    }
    if (nextQuestion && !finished) {
      appendChild(
        list,
        el(
          doc,
          "li",
          {
            "data-panel": "turn",
            "data-role": "ai",
            "data-field": "next-question",
          },
          String(nextQuestion)
        )
      );
    }
    appendChild(thread, list);
    appendChild(root, thread);
    if (finished) {
      appendChild(
        root,
        el(doc, "p", { "data-panel": "thread-finished" }, "Interview finished.")
      );
    }
  }

  function renderSummary(doc, root, summary) {
    clearChildren(root);
    var panel = el(doc, "section", { "data-panel": "summary" });
    appendChild(panel, el(doc, "h2", null, "Interview summary"));
    if (!summary) {
      appendChild(panel, el(doc, "p", null, "No summary available."));
    } else if (typeof summary === "string") {
      appendChild(
        panel,
        el(doc, "p", { "data-field": "summary-text" }, summary)
      );
    } else {
      if (typeof summary.score === "number") {
        appendChild(
          panel,
          el(doc, "p", { "data-field": "score" }, "Score: " + summary.score)
        );
      }
      if (typeof summary.answered_count === "number") {
        appendChild(
          panel,
          el(
            doc,
            "p",
            { "data-field": "answered-count" },
            "Answered: " + summary.answered_count
          )
        );
      }
      if (typeof summary.remaining_count === "number") {
        appendChild(
          panel,
          el(
            doc,
            "p",
            { "data-field": "remaining-count" },
            "Remaining: " + summary.remaining_count
          )
        );
      }
      if (summary.summary_text) {
        appendChild(
          panel,
          el(doc, "p", { "data-field": "summary-text" }, summary.summary_text)
        );
      }
    }
    var link = el(doc, "a", {
      "data-panel": "back-to-dashboard",
      href: "/dashboard",
    }, "Back to dashboard");
    appendChild(panel, link);
    appendChild(root, panel);
  }

  function renderInputArea(doc, root, finished, attemptId, onSubmit, onNext, onEnd) {
    var area = el(doc, "section", { "data-panel": "input-area" });
    if (finished) {
      appendChild(
        area,
        el(doc, "p", { "data-panel": "input-disabled" }, "Interview is finished.")
      );
      appendChild(root, area);
      return;
    }
    var ta = el(doc, "textarea", {
      "data-panel": "answer-input",
      rows: "4",
      cols: "60",
      placeholder: "Type your answer and press Send.",
    });
    if (attemptId != null) {
      ta.setAttribute("data-attempt-id", String(attemptId));
    }
    appendChild(area, ta);
    appendChild(area, el(doc, "br"));
    var sendButton = el(
      doc, "button", { "data-action": "send-answer", type: "button" }, "Send answer"
    );
    var nextButton = el(
      doc, "button", { "data-action": "next-question", type: "button" }, "Next question"
    );
    var endButton = el(
      doc, "button", { "data-action": "end-interview", type: "button" }, "End interview"
    );
    appendChild(area, sendButton);
    appendChild(area, nextButton);
    appendChild(area, endButton);
    appendChild(root, area);
    if (typeof onSubmit === "function") {
      sendButton.addEventListener && sendButton.addEventListener("click", function () {
        onSubmit(ta.value || "");
      });
      ta.addEventListener && ta.addEventListener("keydown", function (ev) {
        if (ev && ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
          ev.preventDefault();
          onSubmit(ta.value || "");
        }
      });
    }
    if (typeof onNext === "function") {
      nextButton.addEventListener && nextButton.addEventListener("click", function () {
        onNext();
      });
    }
    if (typeof onEnd === "function") {
      endButton.addEventListener && endButton.addEventListener("click", function () {
        onEnd();
      });
    }
  }

  function readBody(res) {
    if (!res || typeof res.json !== "function") return Promise.resolve(null);
    return res.json();
  }

  function startInterview(state, quizSetId) {
    var fetchImpl = state.fetchImpl;
    var doc = state.doc;
    var root = state.root;
    var url = API_BASE + "/" + encodeURIComponent(quizSetId) + "/attempts";
    return fetchImpl(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode: "interview" }),
    }).then(function (res) {
      if (handleErrorStatus(doc, root, res)) return null;
      if (res.status === 400) {
        return readBody(res).then(function (body) {
          var detail =
            (body && (body.detail || body.message)) ||
            "Interview mode is not available for this quiz.";
          statusPanel(doc, root, 400, statusTitle(400), detail);
          return null;
        });
      }
      if (res.status === 422) {
        return readBody(res).then(function (body) {
          var detail =
            (body && (body.detail || body.message)) ||
            "The interview service returned an invalid response.";
          statusPanel(doc, root, 422, statusTitle(422), detail);
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
        state.finished = !!body.finished;
        var turns = Array.isArray(body.transcript_so_far) ? body.transcript_so_far : [];
        renderThread(doc, root, turns, state.finished, body.question_text);
        if (state.attemptId != null && !state.finished) {
          renderInputArea(
            doc,
            root,
            false,
            state.attemptId,
            function (text) { return sendAnswer(state, state.attemptId, text); },
            function () { return sendNext(state, state.attemptId); },
            function () { return sendEnd(state, state.attemptId); }
          );
        }
        return body;
      });
    });
  }

  function sendAnswer(state, attemptId, answerText) {
    var fetchImpl = state.fetchImpl;
    var doc = state.doc;
    var root = state.root;
    var url = API_BASE + "/attempts/" + encodeURIComponent(attemptId) + "/interview-turn";
    return fetchImpl(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action: "answer", answer_text: String(answerText || "") }),
    }).then(function (res) {
      if (handleErrorStatus(doc, root, res)) return null;
      if (res.status === 400 || res.status === 409) {
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
        body = body || {};
        state.finished = !!body.finished;
        var turns = Array.isArray(body.transcript_so_far) ? body.transcript_so_far : [];
        if (state.finished) {
          renderSummary(doc, root, body.summary || null);
        } else {
          renderThread(doc, root, turns, false, body.question_text);
          renderInputArea(
            doc,
            root,
            false,
            state.attemptId,
            function (text) { return sendAnswer(state, state.attemptId, text); },
            function () { return sendNext(state, state.attemptId); },
            function () { return sendEnd(state, state.attemptId); }
          );
        }
        return body;
      });
    });
  }

  function sendNext(state, attemptId) {
    var fetchImpl = state.fetchImpl;
    var doc = state.doc;
    var root = state.root;
    var url = API_BASE + "/attempts/" + encodeURIComponent(attemptId) + "/interview-turn";
    return fetchImpl(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action: "next" }),
    }).then(function (res) {
      if (handleErrorStatus(doc, root, res)) return null;
      if (res.status === 400 || res.status === 409) {
        return readBody(res).then(function (body) {
          var detail =
            (body && (body.detail || body.message)) ||
            "Could not advance to the next question.";
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
        state.finished = !!body.finished;
        var turns = Array.isArray(body.transcript_so_far) ? body.transcript_so_far : [];
        if (state.finished) {
          renderSummary(doc, root, body.summary || null);
        } else {
          renderThread(doc, root, turns, false, body.question_text);
          renderInputArea(
            doc,
            root,
            false,
            state.attemptId,
            function (text) { return sendAnswer(state, state.attemptId, text); },
            function () { return sendNext(state, state.attemptId); },
            function () { return sendEnd(state, state.attemptId); }
          );
        }
        return body;
      });
    });
  }

  function sendEnd(state, attemptId) {
    var fetchImpl = state.fetchImpl;
    var doc = state.doc;
    var root = state.root;
    var url = API_BASE + "/attempts/" + encodeURIComponent(attemptId) + "/interview-turn";
    return fetchImpl(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action: "end" }),
    }).then(function (res) {
      if (handleErrorStatus(doc, root, res)) return null;
      if (res.status === 400 || res.status === 409) {
        return readBody(res).then(function (body) {
          var detail =
            (body && (body.detail || body.message)) ||
            "Could not end the interview.";
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
        state.finished = true;
        renderSummary(doc, root, body.summary || null);
        return body;
      });
    });
  }

  function resumeInterview(state, attemptId) {
    var fetchImpl = state.fetchImpl;
    var doc = state.doc;
    var root = state.root;
    var url = API_BASE + "/attempts/" + encodeURIComponent(attemptId) + "/interview";
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
        var turns = Array.isArray(body.turns) ? body.turns : [];
        var finished = !!body.summary;
        state.attemptId = attemptId;
        state.finished = finished;
        renderThread(doc, root, turns, finished);
        if (finished) {
          renderSummary(doc, root, body.summary || null);
        } else {
          renderInputArea(
            doc,
            root,
            false,
            state.attemptId,
            function (text) { return sendAnswer(state, state.attemptId, text); },
            function () { return sendNext(state, state.attemptId); },
            function () { return sendEnd(state, state.attemptId); }
          );
        }
        return body;
      });
    });
  }

  function parseOptions(options) {
    var opts = options || {};
    var quizSetId = opts.quiz_set_id != null ? opts.quiz_set_id : null;
    var attemptId = opts.attempt_id != null ? opts.attempt_id : null;
    if ((!quizSetId && !attemptId) && typeof globalThis !== "undefined" && globalThis.location) {
      try {
        var search = String(globalThis.location.search || "");
        if (search) {
          var params = {};
          var parts = search.replace(/^\?/, "").split("&");
          for (var i = 0; i < parts.length; i++) {
            var pair = parts[i].split("=");
            if (pair.length === 2) params[decodeURIComponent(pair[0])] = decodeURIComponent(pair[1] || "");
          }
          if (!quizSetId && params.quiz_set_id) quizSetId = params.quiz_set_id;
          if (!attemptId && params.attempt_id) attemptId = params.attempt_id;
        }
      } catch (e) {
        // ignore URL parse failures
      }
    }
    return { quizSetId: quizSetId, attemptId: attemptId };
  }

  function initInterview(root, doc, options) {
    if (!root) throw new Error("initInterview: root element is required");
    if (!doc) throw new Error("initInterview: doc is required");
    if (
      typeof doc.createElement !== "function" ||
      typeof doc.createTextNode !== "function"
    ) {
      throw new Error("initInterview: doc must expose createElement and createTextNode");
    }
    var fetchImpl = null;
    if (options && typeof options.fetchImpl === "function") {
      fetchImpl = options.fetchImpl;
    } else if (typeof globalThis !== "undefined" && typeof globalThis.fetch === "function") {
      fetchImpl = globalThis.fetch.bind(globalThis);
    } else if (typeof fetch === "function") {
      fetchImpl = fetch;
    }
    if (!fetchImpl) {
      throw new Error("initInterview: globalThis.fetch is not available");
    }

    var parsed = parseOptions(options);
    var state = {
      root: root,
      doc: doc,
      fetchImpl: fetchImpl,
      quizSetId: parsed.quizSetId,
      attemptId: parsed.attemptId,
      finished: false,
      turns: [],
    };

    clearChildren(root);
    appendChild(
      root,
      el(doc, "p", { "data-panel": "initial" }, "Loading interview\u2026")
    );

    var sraInterview = {
      initInterview: initInterview,
      startInterview: function (id) { return startInterview(state, id); },
      sendAnswer: function (attemptId, answerText) { return sendAnswer(state, attemptId, answerText); },
      sendNext: function (attemptId) { return sendNext(state, attemptId); },
      sendEnd: function (attemptId) { return sendEnd(state, attemptId); },
      resumeInterview: function (attemptId) { return resumeInterview(state, attemptId); },
      renderThread: function (turns, finished) { return renderThread(doc, root, turns, finished); },
      renderSummary: function (summary) { return renderSummary(doc, root, summary); },
      renderInputArea: function (finished, attemptId, onSubmit, onNext, onEnd) {
        return renderInputArea(doc, root, finished, attemptId, onSubmit, onNext, onEnd);
      },
      state: state,
      ready: null,
    };

    try {
      if (typeof globalThis !== "undefined") {
        globalThis.sraInterview = sraInterview;
        globalThis.initInterview = initInterview;
        globalThis.startInterview = sraInterview.startInterview;
        globalThis.sendAnswer = sraInterview.sendAnswer;
        globalThis.sendNext = sraInterview.sendNext;
        globalThis.sendEnd = sraInterview.sendEnd;
        globalThis.resumeInterview = sraInterview.resumeInterview;
      }
      if (typeof window !== "undefined") {
        window.sraInterview = sraInterview;
      }
    } catch (e) {
      // non-browser host: skip global exposure
    }

    var inFlight = null;
    if (state.attemptId != null) {
      inFlight = sraInterview.resumeInterview(state.attemptId);
    } else if (state.quizSetId != null) {
      inFlight = sraInterview.startInterview(state.quizSetId);
    } else {
      clearChildren(root);
      statusPanel(
        doc,
        root,
        400,
        "Missing quiz_set_id (400)",
        "Provide quiz_set_id to start an interview."
      );
      inFlight = Promise.resolve(null);
    }

// `auto_advance` is a test/headless hook: after the start succeeds,
// immediately submit a placeholder answer so the unit-test driver can
// observe the post-answer render. It is OFF in real browser use.
    if (options && options.auto_advance && state.quizSetId != null) {
      inFlight = inFlight.then(function (body) {
        if (!body || state.finished) return body;
        return sendAnswer(state, state.attemptId, options.auto_advance_text || "answer")
          .catch(function (e) { return null; });
      });
    }

    sraInterview.ready = function () { return inFlight; };
    return sraInterview;
  }

  // Expose as a global function so the unit-test driver (which loads the
  // source via `new Function`) and the dashboard's HTML shell can both
  // reach `initInterview` without needing an `export` statement.
  if (typeof globalThis !== "undefined") {
    globalThis.initInterview = initInterview;
  }
  if (typeof window !== "undefined") {
    window.initInterview = initInterview;
  }
})();
