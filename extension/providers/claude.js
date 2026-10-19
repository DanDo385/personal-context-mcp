// Personal Context — Claude.ai provider extractor.
// Uses Claude's internal JSON API (same endpoints the web app loads).
// Auth: session cookies, auto-attached by the browser.

const CLAUDE_ORG_API = 'https://claude.ai/api/organizations';
const CLAUDE_CONVERSATIONS_API = (orgId) =>
  `https://claude.ai/api/organizations/${orgId}/chat_conversations`;
const CLAUDE_CONVERSATION_API = (orgId, convId) =>
  `https://claude.ai/api/organizations/${orgId}/chat_conversations/${convId}?tree=true&rendering_mode=messages&render_all_tools=true`;

let cachedOrgId = null;

/**
 * Detect the chat-capable organization ID.
 */
async function getOrgId() {
  if (cachedOrgId) return cachedOrgId;

  const resp = await fetch(CLAUDE_ORG_API, {
    credentials: 'include',
    headers: { 'Accept': 'application/json' },
  });

  if (!resp.ok) {
    throw new Error(`Failed to fetch organizations: ${resp.status}`);
  }

  const orgs = await resp.json();
  const chatOrg = orgs.find((org) =>
    org.capabilities && org.capabilities.includes('chat')
  );
  cachedOrgId = chatOrg ? chatOrg.uuid : orgs[0]?.uuid;
  if (!cachedOrgId) throw new Error('No organizations found');
  return cachedOrgId;
}

/**
 * List all conversations (for backfill).
 * Returns [{ uuid, name, model, created_at, updated_at, is_temporary }].
 */
async function listConversations() {
  const orgId = await getOrgId();
  const resp = await fetch(CLAUDE_CONVERSATIONS_API(orgId), {
    credentials: 'include',
    headers: { 'Accept': 'application/json' },
  });

  if (!resp.ok) {
    throw new Error(`Failed to list conversations: ${resp.status}`);
  }

  const convs = await resp.json();
  // Filter out temporary chats.
  return convs.filter((c) => !c.is_temporary);
}

/**
 * Fetch one full conversation with all messages.
 * Walks the active lineage from current_leaf_message_uuid.
 */
async function fetchConversation(conversationId) {
  const orgId = await getOrgId();
  const resp = await fetch(CLAUDE_CONVERSATION_API(orgId, conversationId), {
    credentials: 'include',
    headers: { 'Accept': 'application/json' },
  });

  if (!resp.ok) {
    throw new Error(`Failed to fetch conversation ${conversationId}: ${resp.status}`);
  }

  const raw = await resp.json();

  // Build message index by UUID.
  const msgMap = {};
  for (const msg of raw.chat_messages || []) {
    msgMap[msg.uuid] = msg;
  }

  // Walk active lineage from leaf.
  const activeMessages = [];
  let current = raw.current_leaf_message_uuid;
  while (current && msgMap[current]) {
    activeMessages.unshift(msgMap[current]);
    current = msgMap[current].parent_message_uuid;
  }

  // Convert to normalized format.
  const messages = activeMessages.map((msg) => {
    const role = msg.sender === 'human' ? 'user' : 'assistant';
    // Extract text from content blocks.
    const textBlocks = (msg.content || [])
      .filter((b) => b.type === 'text' && b.text)
      .map((b) => b.text);
    const thinkingBlocks = (msg.content || [])
      .filter((b) => b.type === 'thinking' && b.thinking)
      .map((b) => `[thinking] ${b.thinking}`);
    const content = [...textBlocks, ...thinkingBlocks].join('\n\n');

    return {
      provider_message_id: msg.uuid,
      role,
      content,
      created_at: msg.created_at,
      model: role === 'assistant' ? raw.model : null,
    };
  });

  // Extract attachment references.
  const attachments = [];
  for (const msg of activeMessages) {
    for (const att of msg.attachments || []) {
      attachments.push({
        provider_attachment_id: att.id,
        file_name: att.file_name,
        file_type: att.file_type,
        file_size: att.file_size,
      });
    }
  }

  return {
    provider: 'claude',
    provider_conversation_id: raw.uuid,
    title: raw.name || '',
    model: raw.model,
    created_at: raw.created_at,
    updated_at: raw.updated_at,
    messages,
    attachments,
  };
}

/**
 * Extract the currently open conversation from the page context.
 * Used for save-on-click — reads the conversation ID from the URL,
 * then fetches via the API.
 */
async function extractCurrentConversation() {
  const url = new URL(window.location.href);
  const match = url.pathname.match(/\/chat\/([a-zA-Z0-9-]+)/);
  if (!match) return null;

  const conversationId = match[1];
  return fetchConversation(conversationId);
}

export {
  getOrgId, listConversations, fetchConversation, extractCurrentConversation,
};