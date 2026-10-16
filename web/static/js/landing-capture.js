// web/static/js/landing-capture.js
// SRA-900: in-browser study-note capture for the landing page.
//
// Authenticated users can:
//   - paste or type a transcript,
//   - dictate with the browser's built-in Web Speech API, or
//   - submit either source to POST /api/v1/entries/ingest.
//
// The same path serves both the desktop client and the web app:
// `sraJsonFetch` already handles 401-refresh-retry, and the server returns
// the validated entry list (201) which we render below the form.

(function () {
  "use strict";

  const INGEST_URL = "/api/v1/entries/ingest";

  const form = document.getElementById("capture-form");
  if (!form) return;

  const textarea = document.getElementById("capture-transcript");
  const recordBtn = document.getElementById("capture-record");
  const recordLabel = recordBtn ? recordBtn.querySelector(".capture-record-label") : null;
  const recordHint = document.getElementById("capture-record-hint");
  const submitBtn = document.getElementById("capture-submit");
  const clearBtn = document.getElementById("capture-clear");
  const statusEl = document.getElementById("capture-status");
  const resultsEmpty = document.getElementById("capture-results-empty");
  const resultsList = document.getElementById("capture-results-list");

  // --- Web Speech API bootstrap --------------------------------------------

  const SpeechRecognitionCtor =
    window.SpeechRecognition || window.webkitSpeechRecognition || null;
  let recognizer = null;
  let recognizing = false;
  let interimBuffer = "";
  let finalBuffer = "";
  // Track the last result index we have already committed as final, plus the
  // transcript of that result. The Web Speech API may re-deliver an existing
  // final result in a later onresult event (and a new segment's first interim
  // can briefly echo the prior segment's finalized text); both would otherwise
  // duplicate locked-in words in the live transcript.
  let lastFinalIndex = -1;
  let lastFinalTranscript = "";

  function setRecordLabel(state) {
    if (!recordLabel) return;
    recordLabel.setAttribute("data-state", state);
    if (state === "listening") recordLabel.textContent = "Stop recording";
    else if (state === "unsupported") recordLabel.textContent = "Voice capture unavailable";
    else if (state === "denied") recordLabel.textContent = "Mic permission denied";
    else recordLabel.textContent = "Record";
  }

  function setRecordEnabled(enabled) {
    if (!recordBtn) return;
    recordBtn.disabled = !enabled;
  }

  function setStatus(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.className = "status" + (kind ? " " + kind : "");
  }

  function getBaselineText() {
    const value = textarea.value;
    if (!value) return "";
    return value.endsWith(" ") || value === "" ? value : value + " ";
  }

  // Return the portion of `newText` that follows the longest word-aligned
  // overlap with the end of `priorText`. Used to strip already-finalized
  // words from new final results and from the live interim transcript, so
  // repeated or echoed results never duplicate locked-in text.
  function wordsAfterOverlap(priorText, newText) {
    const prior = (priorText || "").trim();
    const next = (newText || "").trim();
    if (!prior || !next) return next;
    const priorWords = prior.split(/\s+/).filter(Boolean);
    const nextWords = next.split(/\s+/).filter(Boolean);
    const maxK = Math.min(priorWords.length, nextWords.length);
    for (let k = maxK; k > 0; k--) {
      let match = true;
      for (let i = 0; i < k; i++) {
        if (priorWords[priorWords.length - k + i].toLowerCase() !== nextWords[i].toLowerCase()) {
          match = false;
          break;
        }
      }
      if (match) return nextWords.slice(k).join(" ");
    }
    return next;
  }

  function buildRecognizer() {
    const recognizerCtor = SpeechRecognitionCtor;
    if (!recognizerCtor) return null;
    const instance = new recognizerCtor();
    instance.continuous = true;
    instance.interimResults = true;
    instance.lang = navigator.language || "en-US";

    instance.onresult = (event) => {
      // The recognizer can re-deliver an existing final result, produce
      // multiple results with the same transcript, or briefly echo prior
      // finalized text at the start of the next interim. To avoid any of
      // those turning into duplicated words in the live transcript, we only
      // commit the *new* word-aligned suffix of each result.
      for (let i = Math.max(event.resultIndex, lastFinalIndex + 1); i < event.results.length; i++) {
        const result = event.results[i];
        if (!result.isFinal) continue;
        const transcript = result[0] && result[0].transcript ? result[0].transcript : "";
        if (!transcript) continue;
        const delta = wordsAfterOverlap(finalBuffer, transcript);
        if (!delta) {
          // Nothing new to commit; still record this result index so a
          // later re-delivery of the same final does not slip through.
          lastFinalIndex = i;
          lastFinalTranscript = transcript;
          continue;
        }
        finalBuffer = finalBuffer + delta + " ";
        lastFinalIndex = i;
        lastFinalTranscript = transcript;
      }
      // The live "what's being recognized now" text is the most recent
      // non-final result, with any overlap against the already-finalized
      // text stripped so the rendered textarea does not duplicate words.
      let latestInterim = "";
      for (let j = event.results.length - 1; j > lastFinalIndex; j--) {
        if (event.results[j].isFinal) continue;
        const t = event.results[j][0] && event.results[j][0].transcript;
        if (t) latestInterim = t;
        break;
      }
      interimBuffer = wordsAfterOverlap(finalBuffer, latestInterim);
      renderTranscript();
    };

    instance.onerror = (event) => {
      const error = event && event.error ? String(event.error) : "error";
      if (error === "not-allowed" || error === "service-not-allowed") {
        setStatus("Microphone permission was denied.", "error");
        setRecordLabel("denied");
      } else if (error === "no-speech") {
        setStatus("No speech detected. Try again.", "error");
      } else {
        setStatus("Voice capture error: " + error, "error");
      }
      stopRecording();
    };

    instance.onend = () => {
      recognizing = false;
      interimBuffer = "";
      finalBuffer = "";
      if (recordBtn) recordBtn.setAttribute("aria-pressed", "false");
      if (recordBtn && !recordBtn.disabled) setRecordLabel("idle");
      renderTranscript();
    };

    return instance;
  }

  function renderTranscript() {
    const baseline = getBaselineText();
    const finalPart = finalBuffer.trim();
    const interimPart = interimBuffer.trim();
    let text = baseline;
    if (finalPart) text += (baseline && !baseline.endsWith(" ") ? " " : "") + finalPart;
    if (interimPart) text += (text && !text.endsWith(" ") ? " " : "") + interimPart;
    textarea.value = text;
  }

  function startRecording() {
    if (!SpeechRecognitionCtor) {
      setStatus("This browser does not support voice capture. You can still type or paste text.", "error");
      return;
    }
    if (recognizing) return;
    recognizer = recognizer || buildRecognizer();
    if (!recognizer) return;
    finalBuffer = "";
    interimBuffer = "";
    lastFinalIndex = -1;
    lastFinalTranscript = "";
    try {
      recognizer.start();
      recognizing = true;
      recordBtn.setAttribute("aria-pressed", "true");
      setRecordLabel("listening");
      setStatus("Listening. Click Record again to stop.", "");
    } catch (err) {
      // start() throws if recognition is already started (e.g. a double-click
      // raced past the guard). Treat that as a no-op, otherwise surface the error.
      if (!recognizing) setStatus("Could not start voice capture: " + (err && err.message ? err.message : String(err)), "error");
    }
  }

  function stopRecording() {
    if (!recognizer || !recognizing) {
      recognizing = false;
      return;
    }
    try {
      recognizer.stop();
    } catch (err) {
      // ignore: onend will reset UI state
    }
  }

  if (recordBtn) {
    if (!SpeechRecognitionCtor) {
      setRecordEnabled(false);
      setRecordLabel("unsupported");
      if (recordHint) recordHint.textContent = "Your browser does not support in-page voice capture. Paste a transcript or type your notes instead.";
    } else {
      recordBtn.addEventListener("click", () => {
        if (recognizing) stopRecording();
        else startRecording();
      });
    }
  }

  // --- submit --------------------------------------------------------------

  async function ingest(transcript) {
    const response = await window.sraJsonFetch(INGEST_URL, {
      method: "POST",
      body: JSON.stringify({ transcript }),
    });
    if (response.status === 401) {
      window.location.href = "/signin?next=/";
      return null;
    }
    let body = null;
    try {
      body = await response.json();
    } catch (err) {
      body = null;
    }
    if (!response.ok) {
      let message = "The server could not save the note.";
      if (body && typeof body === "object" && body.detail) {
        message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      } else if (typeof body === "string" && body.trim()) {
        message = body;
      }
      const err = new Error(message);
      err.status = response.status;
      throw err;
    }
    return body;
  }

  function renderEntries(entries) {
    if (!Array.isArray(entries) || entries.length === 0) {
      if (resultsEmpty) {
        resultsEmpty.textContent =
          "The agent did not extract any topics from that note. Try adding more detail.";
        resultsEmpty.hidden = false;
      }
      if (resultsList) {
        resultsList.replaceChildren();
        resultsList.hidden = true;
      }
      return;
    }
    if (resultsEmpty) resultsEmpty.hidden = true;
    if (!resultsList) return;
    const items = entries.map((entry) => {
      const li = document.createElement("li");
      li.className = "capture-result";

      const header = document.createElement("h4");
      header.className = "capture-result-topic";
      header.textContent = entry.topic || "Untitled";
      li.appendChild(header);

      if (entry.subtopic) {
        const sub = document.createElement("p");
        sub.className = "capture-result-subtopic muted";
        sub.textContent = entry.subtopic;
        li.appendChild(sub);
      }

      const summary = document.createElement("p");
      summary.className = "capture-result-summary";
      summary.textContent = entry.summary || "";
      li.appendChild(summary);

      if (Array.isArray(entry.tags) && entry.tags.length > 0) {
        const tags = document.createElement("ul");
        tags.className = "capture-result-tags";
        for (const tag of entry.tags) {
          const tagLi = document.createElement("li");
          tagLi.className = "capture-result-tag";
          tagLi.textContent = tag;
          tags.appendChild(tagLi);
        }
        li.appendChild(tags);
      }

      return li;
    });
    resultsList.replaceChildren(...items);
    resultsList.hidden = false;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (recognizing) stopRecording();
    const text = (textarea.value || "").trim();
    if (!text) {
      setStatus("Add a transcript (type, paste, or dictate) before capturing.", "error");
      textarea.focus();
      return;
    }
    if (submitBtn) submitBtn.disabled = true;
    setStatus("Saving…", "");
    try {
      const entries = await ingest(text);
      if (entries === null) return; // redirecting to sign-in
      const count = Array.isArray(entries) ? entries.length : 0;
      if (count === 0) {
        setStatus("Saved, but the agent could not extract topics from that note.", "");
      } else {
        setStatus(count === 1 ? "Saved 1 entry." : `Saved ${count} entries.`, "success");
      }
      renderEntries(entries || []);
      textarea.value = "";
    } catch (err) {
      setStatus(err && err.message ? err.message : "Could not save the note.", "error");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      if (recognizing) stopRecording();
      textarea.value = "";
      setStatus("", "");
    });
  }
})();
