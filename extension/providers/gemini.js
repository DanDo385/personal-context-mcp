// Personal Context — Gemini provider extractor.
// DOM-based extraction. Gemini's batchExecute RPC is too fragile for production,
// so we use the DOM structure the page renders. Fallback if RPC approach later proves stable.

/**
 * Detect if we're on a Gemini conversation page.
 */
function isConversationPage() {
  return window.location.pathname.includes('/app/') &&
    document.querySelector('div.conversation-container[id]') !== null;
}

/**
 * Wait for an element to appear in the DOM.
 */
function waitForElement(selector, timeout = 5000) {
  return new Promise((resolve, reject) => {
    const el = document.querySelector(selector);
    if (el) return resolve(el);

    const observer = new MutationObserver(() => {
      const el = document.querySelector(selector);
      if (el) {
        observer.disconnect();
        resolve(el);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => {
      observer.disconnect();
      reject(new Error(`Timeout waiting for ${selector}`));
    }, timeout);
  });
}

/**
 * Scroll the chat history container to load all messages.
 * Gemini lazy-loads content bidirectionally.
 */
async function scrollToLoadAll() {
  const scroller = document.querySelector(
    'infinite-scroller[data-test-id="chat-history-container"]'
  ) || document.querySelector('#chat-history') ||
    document.querySelector('.chat-history-scroll-container');

  if (!scroller) return;

  // Scroll to top to trigger loading of older messages.
  const scrollToTop = () => {
    scroller.scrollTop = 0;
  };

  // Try to load more by scrolling up repeatedly.
  let prevHeight = 0;
  for (let i = 0; i < 20; i++) {
    scrollToTop();
    await new Promise((r) => setTimeout(r, 500));
    const newHeight = scroller.scrollHeight;
    if (newHeight === prevHeight) break;
    prevHeight = newHeight;
  }
}

/**
 * Extract text content from a user-query element.
 */
function extractUserContent(userQuery) {
  const textEl = userQuery.querySelector('.query-text-line');
  if (textEl) return textEl.textContent?.trim() || '';

  const queryText = userQuery.querySelector('.query-text');
  if (queryText) return queryText.textContent?.trim() || '';

  return userQuery.textContent?.trim() || '';
}

/**
 * Extract text content from a model-response element.
 * Strips citations, avatars, and processing states.
 */
function extractAssistantContent(modelResponse) {
  const markdown = modelResponse.querySelector('.markdown.markdown-main-panel');
  if (markdown) {
    const clone = markdown.cloneNode(true);
    // Remove non-content elements.
    const toRemove = [
      'source-footnote', 'source-inline-chip', 'sources-carousel-inline',
      'bard-avatar', 'processing-state', 'tts-control',
      'thumb-up-button', 'thumb-down-button', 'copy-button',
      '.code-block-decoration', 'freemium-rag-disclaimer',
      'sensitive-memories-banner',
    ];
    for (const sel of toRemove) {
      try {
        clone.querySelectorAll(sel).forEach((el) => el.remove());
      } catch { /* invalid selector */ }
    }
    return clone.textContent?.trim() || '';
  }

  const messageContent = modelResponse.querySelector('message-content');
  if (messageContent) return messageContent.textContent?.trim() || '';

  return modelResponse.textContent?.trim() || '';
}

/**
 * Extract the currently open conversation from the DOM.
 */
async function extractCurrentConversation() {
  if (!isConversationPage()) return null;

  // Load all messages by scrolling.
  await scrollToLoadAll();

  // Find conversation containers.
  const containers = document.querySelectorAll('div.conversation-container[id]');
  if (containers.length === 0) return null;

  // Extract conversation ID from URL path.
  const url = new URL(window.location.href);
  const cidMatch = url.pathname.match(/\/([a-zA-Z0-9_-]+)$/);
  const cid = cidMatch ? cidMatch[1] : '';

  // Extract title from the page.
  const titleEl = document.querySelector('header .chat-title') ||
    document.querySelector('[data-test-id="chat-title"]');
  const title = titleEl?.textContent?.trim() || '';

  // Extract messages.
  const messages = [];
  for (const container of containers) {
    const turnId = container.id;

    // User turn.
    const userQuery = container.querySelector('user-query');
    if (userQuery) {
      const content = extractUserContent(userQuery);
      if (content) {
        messages.push({
          provider_message_id: `${turnId}-user`,
          role: 'user',
          content,
          created_at: null,
          model: null,
        });
      }
    }

    // Assistant turn.
    const modelResponse = container.querySelector('model-response');
    if (modelResponse) {
      const content = extractAssistantContent(modelResponse);
      if (content) {
        messages.push({
          provider_message_id: `${turnId}-assistant`,
          role: 'assistant',
          content,
          created_at: null,
          model: null,
        });
      }
    }
  }

  return {
    provider: 'gemini',
    provider_conversation_id: cid,
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
 * Gemini sidebar uses a virtual-scroll list; we extract what's visible.
 */
async function listConversations() {
  // Open the sidebar if collapsed.
  const sidebar = document.querySelector('[data-test-id="side-nav"]') ||
    document.querySelector('nav');
  if (!sidebar) return [];

  const items = sidebar.querySelectorAll('a[href*="/app/"]');
  const convs = [];
  const seen = new Set();

  for (const item of items) {
    const href = item.getAttribute('href') || '';
    const cidMatch = href.match(/\/([a-zA-Z0-9_-]+)$/);
    if (!cidMatch) continue;
    const cid = cidMatch[1];
    if (seen.has(cid)) continue;
    seen.add(cid);

    convs.push({
      id: cid,
      title: item.textContent?.trim() || '',
      created_at: null,
      updated_at: null,
    });
  }

  return convs;
}

export { extractCurrentConversation, listConversations, isConversationPage };