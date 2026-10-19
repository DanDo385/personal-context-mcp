// Personal Context — ChatGPT provider extractor.
// Uses ChatGPT's internal backend API (same endpoints the web app loads).
// Auth: Bearer token from /api/auth/session.

const CHATGPT_SESSION_API = 'https://chatgpt.com/api/auth/session';
const CHATGPT_LIST_API = 'https://chatgpt.com/backend-api/conversations';
const CHATGPT_CONVERSATION_API = (id) =>
  `https://chatgpt.com/backend-api/conversation/${id}`;
const CHATGPT_BATCH_API = 'https://chatgpt.com/backend-api/conversations/batch';

let cachedToken = null;

/**
 * Get the Bearer token from the session endpoint.
 */
async function getToken() {
  if (cachedToken) return cachedToken;

  const resp = await fetch(CHATGPT_SESSION_API, {
    credentials: 'include',
  });

  if (!resp.ok) {
    throw new Error(`Failed to get session: ${resp.status}`);
  }

  const session = await resp.json();
  cachedToken = session.accessToken;
  if (!cachedToken) throw new Error('No access token in session');
  return cachedToken;
}

/**
 * List all conversations with pagination.
 * Returns [{ id, title, create_time, update_time }].
 */
async function listConversations() {
  const token = await getToken();
  const all = [];

  let offset = 0;
  const limit = 100;
  while (true) {
    const url = `${CHATGPT_LIST_API}?offset=${offset}&limit=${limit}&order=updated&hide_snorlax=false`;
    const resp = await fetch(url, {
      credentials: 'include',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/json',
      },
    });

    if (!resp.ok) {
      throw new Error(`Failed to list conversations at offset ${offset}: ${resp.status}`);
    }

    const data = await resp.json();
    const items = data.items || [];
    all.push(...items);

    // total is offset + items + 1, a "there's more" hint.
    if (items.length < limit) break;
    offset += limit;
  }

  return all;
}

/**
 * Walk the message tree from current_node to root.
 * Returns active-lineage messages in order.
 */
function walkActiveLineage(mapping, currentNodeId) {
  const messages = [];
  let current = currentNodeId;

  while (current && mapping[current]) {
    const node = mapping[current];
    const msg = node.message;
    if (msg && msg.author && msg.content) {
      const role = msg.author.role;
      const parts = (msg.content.parts || [])
        .filter((p) => typeof p === 'string')
        .join('\n');
      messages.unshift({
        provider_message_id: msg.id,
        role: role === 'tool' ? 'tool' : role,
        content: parts,
        created_at: msg.create_time ? new Date(msg.create_time * 1000).toISOString() : null,
        model: msg.metadata?.model_slug || null,
      });
    }
    current = node.parent;
  }

  return messages;
}

/**
 * Fetch one full conversation.
 */
async function fetchConversation(conversationId) {
  const token = await getToken();
  const resp = await fetch(CHATGPT_CONVERSATION_API(conversationId), {
    credentials: 'include',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Accept': 'application/json',
    },
  });

  if (!resp.ok) {
    throw new Error(`Failed to fetch conversation ${conversationId}: ${resp.status}`);
  }

  const raw = await resp.json();
  const messages = walkActiveLineage(raw.mapping || {}, raw.current_node);

  // Extract attachments from message metadata.
  const attachments = [];
  for (const nodeId of Object.keys(raw.mapping || {})) {
    const node = raw.mapping[nodeId];
    for (const att of node.message?.metadata?.attachments || []) {
      attachments.push({
        provider_attachment_id: att.id,
        file_name: att.name,
        file_type: att.mime_type,
        file_size: att.size,
      });
    }
  }

  return {
    provider: 'chatgpt',
    provider_conversation_id: raw.conversation_id || conversationId,
    title: raw.title || '',
    model: raw.default_model_slug || null,
    created_at: raw.create_time
      ? new Date(raw.create_time * 1000).toISOString()
      : null,
    updated_at: raw.update_time
      ? new Date(raw.update_time * 1000).toISOString()
      : null,
    messages,
    attachments,
  };
}

/**
 * Fetch multiple conversations via batch endpoint (max 10 at a time).
 */
async function fetchConversationsBatch(ids) {
  if (ids.length === 0) return [];
  if (ids.length > 10) {
    throw new Error('Batch limited to 10 conversations at a time');
  }

  const token = await getToken();
  const resp = await fetch(CHATGPT_BATCH_API, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({ conversation_ids: ids }),
  });

  if (!resp.ok) {
    throw new Error(`Batch fetch failed: ${resp.status}`);
  }

  const data = await resp.json();
  const results = [];
  for (const item of data.items || data || []) {
    const messages = walkActiveLineage(item.mapping || {}, item.current_node);
    results.push({
      provider: 'chatgpt',
      provider_conversation_id: item.conversation_id || item.id,
      title: item.title || '',
      model: item.default_model_slug || null,
      created_at: item.create_time
        ? new Date(item.create_time * 1000).toISOString()
        : null,
      updated_at: item.update_time
        ? new Date(item.update_time * 1000).toISOString()
        : null,
      messages,
      attachments: [],
    });
  }
  return results;
}

/**
 * Extract the currently open conversation from the page context.
 */
async function extractCurrentConversation() {
  const url = new URL(window.location.href);
  const match = url.pathname.match(/\/c\/([a-zA-Z0-9-]+)/);
  if (!match) return null;

  return fetchConversation(match[1]);
}

export {
  getToken, listConversations, fetchConversation,
  fetchConversationsBatch, extractCurrentConversation,
};