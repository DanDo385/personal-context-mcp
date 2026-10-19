// Personal Context — dedup helpers for client-side use.
// Computes content hashes for message deduplication and position-based IDs.

/**
 * Hash a string using SHA-256 (truncated to 32 hex chars).
 * Uses the Web Crypto API available in service workers and content scripts.
 */
async function sha256(text) {
  const encoder = new TextEncoder();
  const data = encoder.encode(text);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
}

/**
 * Generate a deterministic provider_message_id from position and content hash.
 * Used when the provider doesn't give a message ID (e.g., Gemini DOM extraction).
 */
async function positionBasedId(provider, conversationId, index, content) {
  const contentHash = await sha256(content);
  return `${provider}:${conversationId}:${index}:${contentHash.slice(0, 8)}`;
}

/**
 * Generate a deterministic provider_message_id from content alone.
 * Used as last resort fallback.
 */
async function contentBasedId(content) {
  return await sha256(content);
}

export { sha256, positionBasedId, contentBasedId };