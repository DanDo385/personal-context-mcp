// Personal Context — content script.
// Runs in each provider tab. Detects the provider, listens for page changes,
// and extracts conversation data on demand (user click or auto-capture).

(function () {
  'use strict';

  // Prevent double-injection.
  if (window.__personalContextLoaded) return;
  window.__personalContextLoaded = true;

  /**
   * Detect which provider we're on.
   */
  function detectProvider() {
    const host = window.location.hostname;
    if (host.includes('claude.ai')) return 'claude';
    if (host.includes('chatgpt.com')) return 'chatgpt';
    if (host.includes('gemini.google.com')) return 'gemini';
    if (host.includes('grok.com')) return 'grok';
    return null;
  }

  const provider = detectProvider();
  if (!provider) return;

  console.log(`[PersonalContext] Detected provider: ${provider}`);

  // --- Message extraction (called on demand) ---

  /**
   * Extract the current conversation and send to the service worker for queuing.
   */
  async function captureCurrent() {
    try {
      let data = null;

      switch (provider) {
        case 'claude': {
          const mod = await import(chrome.runtime.getURL('providers/claude.js'));
          data = await mod.extractCurrentConversation();
          break;
        }
        case 'chatgpt': {
          const mod = await import(chrome.runtime.getURL('providers/chatgpt.js'));
          data = await mod.extractCurrentConversation();
          break;
        }
        case 'gemini': {
          const mod = await import(chrome.runtime.getURL('providers/gemini.js'));
          data = await mod.extractCurrentConversation();
          break;
        }
        case 'grok': {
          const mod = await import(chrome.runtime.getURL('providers/grok.js'));
          data = await mod.extractCurrentConversation();
          break;
        }
      }

      if (data && data.messages && data.messages.length > 0) {
        chrome.runtime.sendMessage({
          action: 'capture',
          payload: data,
        });
        console.log(`[PersonalContext] Captured ${data.messages.length} messages from ${data.title || 'untitled'}`);
      }
    } catch (err) {
      console.error('[PersonalContext] Capture failed:', err);
      chrome.runtime.sendMessage({
        action: 'captureError',
        error: err.message,
        provider,
      });
    }
  }

  // --- Message listener: respond to popup/service worker requests ---

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (request.action === 'capture') {
      captureCurrent().then(() => sendResponse({ ok: true }));
      return true; // async response
    }
    if (request.action === 'ping') {
      sendResponse({ provider, url: window.location.href });
      return false;
    }
  });

  // --- Auto-capture: detect page changes (new conversation, finished reply) ---

  let lastUrl = window.location.href;
  let debounceTimer = null;

  /**
   * Check if a reply just finished (for auto-capture trigger).
   * Implementation varies by provider.
   */
  function replyJustFinished() {
    switch (provider) {
      case 'claude':
        // Claude: streaming indicator disappears.
        return !document.querySelector('[data-is-streaming="true"]');
      case 'chatgpt':
        // ChatGPT: result-streaming class removed.
        return !document.querySelector('.result-streaming');
      case 'gemini':
        // Gemini: processing-state element disappears.
        return !document.querySelector('processing-state');
      case 'grok':
        // Grok: check if message content has stabilized.
        // We use a simple heuristic: no new response- containers in the last 2s.
        return true; // Always consider "finished" — captures on timer.
      default:
        return false;
    }
  }

  /**
   * Auto-capture: triggered when a reply finishes or URL changes.
   * Debounced to avoid capturing mid-stream.
   */
  function scheduleAutoCapture() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      // Check pause state.
      const { isPaused } = await chrome.storage.local.get('isPaused');
      if (isPaused) return;

      if (replyJustFinished()) {
        captureCurrent();
      }
    }, 2000); // 2s debounce after last change.
  }

  // Watch for URL changes (new conversation opened).
  const urlObserver = new MutationObserver(() => {
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      scheduleAutoCapture();
    }
  });

  // Watch for DOM changes (new messages, finished streaming).
  const domObserver = new MutationObserver(() => {
    scheduleAutoCapture();
  });

  // Start observers.
  urlObserver.observe(document.body, { childList: true, subtree: true });
  domObserver.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });

  console.log('[PersonalContext] Content script ready');
})();