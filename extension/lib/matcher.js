// Personal Context — synthetic session matcher.
// Client-side matching is minimal; real matching happens on the server
// via reconcile-sessions. This module provides helpers for the backfill
// dry-run reporting.

/**
 * Compute title similarity (normalized Levenshtein distance).
 */
function titleSimilarity(a, b) {
  if (!a || !b) return 0;
  const s1 = a.toLowerCase().trim();
  const s2 = b.toLowerCase().trim();
  if (s1 === s2) return 1.0;

  // Simple Jaccard on word tokens.
  const words1 = new Set(s1.split(/\s+/));
  const words2 = new Set(s2.split(/\s+/));
  const intersection = new Set([...words1].filter((w) => words2.has(w)));
  const union = new Set([...words1, ...words2]);
  return union.size === 0 ? 0 : intersection.size / union.size;
}

/**
 * Check if two timestamps are within the same day (±12 hours).
 */
function sameDay(ts1, ts2) {
  if (!ts1 || !ts2) return false;
  const d1 = new Date(ts1);
  const d2 = new Date(ts2);
  return Math.abs(d1.getTime() - d2.getTime()) < 24 * 60 * 60 * 1000;
}

export { titleSimilarity, sameDay };