// SRA-1201 — one calm, shared browser speech layer for authenticated pages.
(function () {
  "use strict";

  const playButton = document.querySelector('[data-action="play-welcome"]');
  const stopButton = document.querySelector('[data-action="stop-voice"]');
  const status = document.querySelector("[data-voice-status]");
  const displayName = (document.body.dataset.userName || "").trim();
  const firstName = displayName.split(/\s+/).filter(Boolean)[0] || "there";
  const speech = window.speechSynthesis;
  const Utterance = window.SpeechSynthesisUtterance;
  let enabled = false;
  let timezone = "UTC";
  let pendingStartTimer = null;

  function setStatus(message) {
    if (status) status.textContent = message;
  }

  function isAvailable() {
    return Boolean(speech && typeof speech.speak === "function" && typeof Utterance === "function");
  }

  function localDateKey() {
    try {
      const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: timezone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).formatToParts(new Date());
      const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
      return `${values.year}-${values.month}-${values.day}`;
    } catch (error) {
      return new Date().toISOString().slice(0, 10);
    }
  }

  function welcomeText() {
    return `Hello ${firstName}. Let's begin our wonderful day with one thoughtful moment of learning.`;
  }

  function speak(text, label) {
    if (!enabled) {
      setStatus("Website voice is turned off in Settings.");
      return false;
    }
    if (!isAvailable()) {
      setStatus("Voice is not available in this browser. Everything else is ready.");
      return false;
    }
    speech.cancel();
    if (pendingStartTimer) window.clearTimeout(pendingStartTimer);
    const utterance = new Utterance(text);
    utterance.rate = 0.94;
    utterance.pitch = 1;
    utterance.volume = 0.82;
    utterance.onstart = () => {
      if (pendingStartTimer) window.clearTimeout(pendingStartTimer);
      pendingStartTimer = null;
      setStatus(label || "Speaking…");
    };
    utterance.onend = () => {
      if (pendingStartTimer) window.clearTimeout(pendingStartTimer);
      pendingStartTimer = null;
      setStatus("Voice ready.");
    };
    utterance.onerror = () => setStatus("Your browser paused the welcome. Play it whenever you're ready.");
    speech.speak(utterance);
    setStatus(label || "Speaking…");
    pendingStartTimer = window.setTimeout(() => {
      pendingStartTimer = null;
      setStatus("Your browser paused the welcome. Play it whenever you're ready.");
    }, 1200);
    return true;
  }

  function playWelcome(options) {
    const force = Boolean(options && options.force);
    const key = `sra-welcome:${localDateKey()}`;
    if (!force && window.localStorage.getItem(key) === "played") {
      setStatus("Today's welcome is ready to replay.");
      return;
    }
    if (speak(welcomeText(), "Sharing today's welcome…")) {
      window.localStorage.setItem(key, "played");
    }
  }

  function stopVoice(message) {
    if (pendingStartTimer) window.clearTimeout(pendingStartTimer);
    pendingStartTimer = null;
    if (speech && typeof speech.cancel === "function") speech.cancel();
    setStatus(message || "Voice stopped.");
  }

  if (playButton) playButton.addEventListener("click", () => playWelcome({ force: true }));
  if (stopButton) stopButton.addEventListener("click", () => stopVoice());

  window.addEventListener("sra:audio-preference", (event) => {
    enabled = Boolean(event.detail && event.detail.enabled);
    if (!enabled) stopVoice("Website voice is off.");
    else setStatus("Website voice is on.");
  });

  window.addEventListener("study:completed", (event) => {
    const kind = event.detail && event.detail.kind;
    const message = kind === "assessment"
      ? "Wonderful work. Your assessment is complete. Take a moment to notice what became clearer."
      : "Well done. Today's review is complete, and your steady practice is adding up.";
    speak(message, "Celebrating your progress…");
  });

  if (!isAvailable()) {
    setStatus("Voice is not available in this browser. Everything else is ready.");
  }

  const fetchPreferences = window.sraJsonFetch || window.fetch.bind(window);
  fetchPreferences("/api/v1/me/preferences").then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }).then((preferences) => {
    enabled = Boolean(preferences.audio_enabled);
    timezone = preferences.timezone || "UTC";
    if (!enabled) {
      setStatus("Website voice is turned off in Settings.");
      return;
    }
    if (!isAvailable()) {
      setStatus("Voice is not available in this browser. Everything else is ready.");
      return;
    }
    playWelcome({ force: false });
  }).catch(() => {
    setStatus("Voice preferences could not be loaded. The page remains available.");
  });
})();
