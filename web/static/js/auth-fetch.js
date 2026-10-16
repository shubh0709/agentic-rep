// web/static/js/auth-fetch.js
// Shared auth-aware fetch wrapper for the browser dashboard pages.
// SRA-306: on 401 from a study route, silently POST /api/v1/auth/refresh
// (the browser's HttpOnly refresh cookie rides automatically) and retry
// the original request once. A second 401 falls through so the caller can
// run its existing redirect-to-/signin behavior. The refresh endpoint
// itself never loops: a 401 from /auth/refresh is returned to the caller
// as-is.

(function () {
  "use strict";

  const REFRESH_URL = "/api/v1/auth/refresh";

  async function parseErrorBody(response) {
    try {
      const body = await response.json();
      if (body && typeof body === "object" && "detail" in body) return body.detail;
    } catch (err) {
      // Not JSON; fall through.
    }
    return response.statusText || "";
  }

  async function refreshSession() {
    // The browser's `sra_refresh` HttpOnly cookie travels automatically with
    // same-origin credentials. We send no body; the server rotates the
    // jti and rewrites both cookies in the response.
    return fetch(REFRESH_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
  }

  async function sraJsonFetch(url, init) {
    const headers = {
      Accept: "application/json",
      ...(init && init.body ? { "Content-Type": "application/json" } : {}),
      ...(init && init.headers ? init.headers : {}),
    };
    const first = await fetch(url, {
      ...init,
      credentials: "same-origin",
      headers,
    });
    if (first.status !== 401) return first;

    const refresh = await refreshSession();
    if (!refresh.ok) return first;

    return fetch(url, {
      ...init,
      credentials: "same-origin",
      headers,
    });
  }

  window.sraJsonFetch = sraJsonFetch;
})();
