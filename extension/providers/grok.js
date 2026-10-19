// Personal Context — Grok provider extractor.
// DOM-based extraction. Grok's REST API is poorly documented;
// DOM extraction with multi-signal role detection is more reliable.

/**
 * Detect if we're on a Grok conversation page.
 */
function isConversationPage() {
  return window.location.pathname.includes('/chat/') &&
    document.querySelector('div[id^="response-"]') !== null;
}

/**
 * Scroll the chat container to load all messages.
 */
async function scrollToLoadAll() {
  const scroller = document.querySelector(
    '.w-full.h-full.overflow-y-auto.overflow-x-hidden'
  ) || document.querySelector('[class*="overflow-y-auto"][class*="scrollbar-gutter"]');

  if (!scroller) return;

  let prevHeight = 0;
  for (let i = 0; i < 20; i++) {
    scroller.scrollTop = 0;
    await new Promise((r) => setTimeout(r, 500));
    const newHeight = scroller.scrollHeight;
    if (newHeight === prevHeight) break;
    prevHeight = newHeight;
  }
}

/**
 * Detect the role of a message container using multiple signals.
 * Signal 1: Container alignment (items-end = user, items-start = assistant)
 * Signal 2: Button count (≤3 = user, ≥5 = assistant)
 * Signal 3: Bubble styling (bg-surface-l1 = user)
 * Signal 4: Edit button present → user
 * Signal 5: Regenerate button present → assistant
 * Signal 6: Response time indicator → assistant
 */
function detectRole(container) {
  const signals = { user: 0, assistant: 0 };
  const classes = container.className || '';

  // Signal 1: Alignment.
  if (classes.includes('items-end')) signals.user += 2;
  else if (classes.includes('items-start')) signals.assistant += 2;

  const flexChild = container.querySelector('[class*="items-end"]');
  const flexChildStart = container.querySelector('[class*="items-start"]');
  if (flexChild && !flexChildStart) signals.user += 1;
  if (flexChildStart && !flexChild) signals.assistant += 1;

  // Signal 2: Button count.
  const buttons = container.querySelectorAll('button');
  if (buttons.length <= 3) signals.user += 2;
  else if (buttons.length >= 5) signals.assistant += 2;

  // Signal 3: Bubble styling.
  if (container.querySelector('[class*="bg-surface-l1"]')) signals.user += 1;
  if (container.querySelector('[class*="max-w-none"]')) signals.assistant += 1;

  // Signal 4: Edit button (user only).
  if (container.querySelector('button[aria-label="Edit"]')) signals.user += 2;

  // Signal 5: Regenerate button (assistant only).
  if (container.querySelector('button[aria-label="Regenerate"]')) signals.assistant += 2;

  // Signal 6: Response time indicator (assistant only).
  const responseTime = container.querySelector('[class*="text-xs"]');
  if (responseTime && /\d+ms/.test(responseTime.textContent || '')) {
    signals.assistant += 1;
  }

  if (signals.user > signals.assistant) return 'user';
  if (signals.assistant > signals.user) return 'assistant';
  return null;
}

/**
 * Extract text content from a message container.
 */
function extractContent(container) {
  const markdown = container.querySelector('.message-bubble .response-content-markdown');
  if (markdown) return markdown.textContent?.trim() || '';

  const markdownAlt = container.querySelector('.markdown');
  if (markdownAlt) return markdownAlt.textContent?.trim() || '';

  // Fallback: clone and remove non-content elements.
  const clone = container.cloneNode(true);
  clone.querySelectorAll(
    'button, [class*="follow-up"], #last-reply-container'
  ).forEach((el) => el.remove());
  return clone.textContent?.trim() || '';
}

/**
 * Extract the currently open conversation from the DOM.
 */
async function extractCurrentConversation() {
  if (!isConversationPage()) return null;

  await scrollToLoadAll();

  const containers = document.querySelectorAll('div[id^="response-"]');
  if (containers.length === 0) return null;

  // Extract conversation ID from URL.
  const url = new URL(window.location.href);
  const idMatch = url.pathname.match(/\/chat\/([a-zA-Z0-9_-]+)/);
  const convId = idMatch ? idMatch[1] : '';

  // Extract title from page.
  const titleEl = document.querySelector('h1') ||
    document.querySelector('[data-testid="chat-title"]');
  const title = titleEl?.textContent?.trim() || '';

  // Extract messages.
  const messages = [];
  for (const container of containers) {
    const turnId = container.id; // "response-{uuid}"
    const role = detectRole(container);
    if (!role) continue;

    const content = extractContent(container);
    if (!content) continue;

    messages.push({
      provider_message_id: turnId,
      role,
      content,
      created_at: null,
      model: null,
    });
  }

  return {
    provider: 'grok',
    provider_conversation_id: convId,
    title,
    model: null,
    created_at: null,
    updated_at: null,
    messages,
    attachments: [],
  };
}

/**
 * List conversations from the sidebar (for backfill).
 */
async function listConversations() {
  const sidebar = document.querySelector('nav') ||
    document.querySelector('[class*="sidebar"]');
  if (!sidebar) return [];

  // Grok sidebar uses links to individual chats.
  const items = sidebar.querySelectorAll('a[href*="/chat/"]');
  const convs = [];
  const seen = new Set();

  for (const item of items) {
    const href = item.getAttribute('href') || '';
    const match = href.match(/\/chat\/([a-zA-Z0-9_-]+)/);
    if (!match) continue;
    const id = match[1];
    if (seen.has(id)) continue;
    seen.add(id);

    convs.push({
      id,
      title: item.textContent?.trim() || '',
      created_at: null,
      updated_at: null,
    });
  }

  return convs;
}

export { extractCurrentConversation, listConversations, isConversationPage };