// web/static/js/settings.js
// Vanilla script for the preferences form. PUTs to /api/v1/me/preferences
// and surfaces server-side validation errors inline.

(function () {
  "use strict";

  const form = document.getElementById("preferences-form");
  const status = document.getElementById("preferences-status");
  if (!form || !status) return;

  function fieldNames() {
    return {
      preferred_review_time: "Preferred review time",
      timezone: "Timezone",
      quiz_question_count: "Questions per quiz",
      quiz_option_count: "Options per question",
    };
  }

  function readForm() {
    return {
      preferred_review_time: form.preferred_review_time.value.trim(),
      timezone: form.timezone.value.trim(),
      quiz_question_count: Number(form.quiz_question_count.value),
      quiz_option_count: Number(form.quiz_option_count.value),
      audio_enabled: Boolean(form.audio_enabled && form.audio_enabled.checked),
    };
  }

  function setStatus(text, kind) {
    status.textContent = text;
    status.className = "status" + (kind ? " " + kind : "");
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setStatus("Saving…");
    const payload = readForm();
    try {
      const res = await window.sraJsonFetch("/api/v1/me/preferences", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      if (res.status === 401) {
        window.location.href = "/signin?next=/settings";
        return;
      }
      if (res.status === 400) {
        const detail = await res.json().catch(() => null);
        const labels = fieldNames();
        const messages = Array.isArray(detail?.detail)
          ? detail.detail.map((d) => `${labels[d.field] || d.field}: ${d.message}`)
          : ["The server rejected one or more fields."];
        setStatus(messages.join(" · "), "error");
        return;
      }
      if (!res.ok) {
        setStatus(`Save failed (HTTP ${res.status}).`, "error");
        return;
      }
      const saved = await res.json();
      form.quiz_question_count.value = saved.quiz_question_count;
      form.quiz_option_count.value = saved.quiz_option_count;
      form.preferred_review_time.value = saved.preferred_review_time;
      form.timezone.value = saved.timezone;
      if (form.audio_enabled) form.audio_enabled.checked = Boolean(saved.audio_enabled);
      window.dispatchEvent(new CustomEvent("sra:audio-preference", {
        detail: { enabled: Boolean(saved.audio_enabled) },
      }));
      setStatus("Preferences saved.", "success");
    } catch (err) {
      setStatus("Network error: " + (err && err.message ? err.message : String(err)), "error");
    }
  });
})();
