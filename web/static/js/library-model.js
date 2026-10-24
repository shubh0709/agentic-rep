(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.sraLibraryModel = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const DAY_MS = 86400000;

  function normalizeTopic(value) {
    return String(value || "").trim().replace(/\s+/g, " ").toLowerCase();
  }

  function dayDelta(date, today) {
    return Math.round(
      (Date.parse(String(date) + "T00:00:00Z") - Date.parse(String(today) + "T00:00:00Z")) / DAY_MS
    );
  }

  function buildTopicGroups(entries, dueReviews, upcomingDays, todayIso) {
    const grouped = new Map();
    const sortedEntries = (Array.isArray(entries) ? entries : []).slice().sort((a, b) =>
      String(b.created_at || "").localeCompare(String(a.created_at || ""))
    );

    for (const entry of sortedEntries) {
      const key = normalizeTopic(entry.topic);
      if (!key) continue;
      if (!grouped.has(key)) grouped.set(key, { key, entries: [] });
      grouped.get(key).entries.push(entry);
    }

    const reviews = [];
    for (const review of Array.isArray(dueReviews) ? dueReviews : []) reviews.push(review);
    for (const day of Array.isArray(upcomingDays) ? upcomingDays : []) {
      for (const review of Array.isArray(day.reviews) ? day.reviews : []) {
        reviews.push({ ...review, scheduled_date: review.scheduled_date || day.date });
      }
    }

    return Array.from(grouped.values()).map((group) => {
      const newest = group.entries[0];
      const entryIds = new Set(group.entries.map((entry) => String(entry.id)));
      const dates = new Map();
      for (const review of reviews) {
        const entryId = review && review.entry ? review.entry.id : review && review.entry_id;
        const date = review && review.scheduled_date;
        if (!entryIds.has(String(entryId)) || !date) continue;
        dates.set(date, (dates.get(date) || 0) + 1);
      }
      const reviewDates = Array.from(dates, ([date, count]) => ({ date, count }))
        .sort((a, b) => a.date.localeCompare(b.date));
      const tags = [];
      const seenTags = new Set();
      for (const entry of group.entries) {
        for (const tag of Array.isArray(entry.tags) ? entry.tags : []) {
          const normalizedTag = String(tag).trim().toLowerCase();
          if (normalizedTag && !seenTags.has(normalizedTag)) {
            seenTags.add(normalizedTag);
            tags.push(tag);
          }
        }
      }
      const summaryEntry = group.entries.find((entry) => String(entry.summary || "").trim());
      const searchText = group.entries.flatMap((entry) => [
        entry.topic, entry.subtopic, entry.summary, entry.raw_transcript,
        ...(Array.isArray(entry.tags) ? entry.tags : []),
      ]).join(" ").toLowerCase();
      const nextReviewDate = reviewDates.length ? reviewDates[0].date : null;
      return {
        key: group.key,
        topic: String(newest.topic || "").trim().replace(/\s+/g, " "),
        entries: group.entries,
        noteCount: group.entries.length,
        summary: summaryEntry ? summaryEntry.summary : "",
        tags,
        lastCapturedAt: newest.created_at || "",
        reviewDates,
        nextReviewDate,
        readyNow: Boolean(nextReviewDate && dayDelta(nextReviewDate, todayIso) <= 0),
        searchText,
      };
    });
  }

  function bucketFor(group, todayIso) {
    if (!group.nextReviewDate) return "none";
    const delta = dayDelta(group.nextReviewDate, todayIso);
    if (delta <= 0) return "ready";
    if (delta <= 7) return "next7";
    if (delta <= 30) return "next30";
    return "later";
  }

  function buildReviewHorizon(groups, todayIso) {
    const result = {
      ready: { count: 0, topicKeys: [] },
      next7: { count: 0, topicKeys: [] },
      next30: { count: 0, topicKeys: [] },
      later: { count: 0, topicKeys: [] },
    };
    for (const group of Array.isArray(groups) ? groups : []) {
      const bucket = bucketFor(group, todayIso);
      if (!result[bucket]) continue;
      result[bucket].count += 1;
      result[bucket].topicKeys.push(group.key);
    }
    return result;
  }

  function filterAndSortTopics(groups, options, todayIso) {
    const settings = options || {};
    const query = String(settings.query || "").trim().toLowerCase();
    const filter = settings.filter || "all";
    const result = (Array.isArray(groups) ? groups : []).filter((group) => {
      if (query && !String(group.searchText || "").includes(query)) return false;
      if (filter === "all") return true;
      return bucketFor(group, todayIso) === filter;
    });
    const originalOrder = new Map(result.map((group, index) => [group.key, index]));
    result.sort((a, b) => {
      let comparison = 0;
      if (settings.sort === "az") comparison = a.key.localeCompare(b.key);
      else if (settings.sort === "recent") comparison = String(b.lastCapturedAt).localeCompare(String(a.lastCapturedAt));
      else {
        if (!a.nextReviewDate && b.nextReviewDate) comparison = 1;
        else if (a.nextReviewDate && !b.nextReviewDate) comparison = -1;
        else comparison = String(a.nextReviewDate || "").localeCompare(String(b.nextReviewDate || ""));
      }
      return comparison || originalOrder.get(a.key) - originalOrder.get(b.key);
    });
    return result;
  }

  return { normalizeTopic, buildTopicGroups, buildReviewHorizon, filterAndSortTopics };
});
