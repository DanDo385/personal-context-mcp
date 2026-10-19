// Personal Context — service worker.
// Handles capture queue processing, periodic sync alarms, and backfill coordination.

import { enqueue, processOne, getStatus, QUEUE_KEY, DEAD_LETTER_KEY } from './lib/queue.js';
import { getServerUrl } from './lib/api.js';

// Alarm names.
const PROCESS_ALARM = 'processQueue';
const SYNC_ALARM = 'periodicSync';

// --- Lifecycle ---

chrome.runtime.onInstalled.addListener(() => {
  console.log('[PersonalContext] Extension installed');

  // Create alarm for queue processing (every 30s when items exist).
  chrome.alarms.create(PROCESS_ALARM, { periodInMinutes: 0.5 });

  // Create alarm for periodic sync (every 15 minutes).
  chrome.alarms.create(SYNC_ALARM, { periodInMinutes: 15 });

  // Set default server URL.
  chrome.storage.local.get('serverUrl', (result) => {
    if (!result.serverUrl) {
      chrome.storage.local.set({
        serverUrl: 'https://mbp.tailfbd394.ts.net',
        fallbackUrl: '',
      });
    }
  });
});

// --- Alarm handler ---

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === PROCESS_ALARM) {
    const { isPaused } = await chrome.storage.local.get('isPaused');
    if (isPaused) return;

    // Process up to 5 items per alarm tick.
    for (let i = 0; i < 5; i++) {
      const processed = await processOne();
      if (!processed) break;
      // Small delay between items to avoid overwhelming the server.
      await new Promise((r) => setTimeout(r, 1000));
    }
  }

  if (alarm.name === SYNC_ALARM) {
    const { isPaused } = await chrome.storage.local.get('isPaused');
    if (isPaused) return;

    // Trigger capture in all open provider tabs.
    const tabs = await chrome.tabs.query({});
    const providerHosts = [
      'claude.ai', 'chatgpt.com',
      'gemini.google.com', 'grok.com',
    ];

    for (const tab of tabs) {
      try {
        const url = new URL(tab.url || '');
        if (providerHosts.some((h) => url.hostname.includes(h))) {
          chrome.tabs.sendMessage(tab.id, { action: 'capture' }).catch(() => {
            // Tab may not have content script loaded — ignore.
          });
        }
      } catch { /* skip malformed URLs */ }
    }
  }
});

// --- Message handler ---

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'capture') {
    const payload = request.payload;
    // Tag with timestamp.
    payload._capturedAt = new Date().toISOString();
    enqueue(payload).then(() => {
      // Update last sync time for this provider.
      const provider = payload.provider;
      chrome.storage.local.get('lastSync', (result) => {
        const lastSync = result.lastSync || {};
        lastSync[provider] = new Date().toISOString();
        chrome.storage.local.set({ lastSync });
      });
      sendResponse({ ok: true });
    }).catch((err) => {
      console.error('[PersonalContext] Enqueue failed:', err);
      sendResponse({ ok: false, error: err.message });
    });
    return true; // async
  }

  if (request.action === 'captureError') {
    console.error(`[PersonalContext] Capture error on ${request.provider}:`, request.error);
    sendResponse({ ok: true });
    return false;
  }

  if (request.action === 'getStatus') {
    getStatus().then(sendResponse);
    return true;
  }

  if (request.action === 'setPaused') {
    chrome.storage.local.set({ isPaused: request.paused });
    sendResponse({ ok: true });
    return false;
  }

  if (request.action === 'backfill') {
    handleBackfill(request.provider, request.dryRun)
      .then(sendResponse)
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
});

// --- Backfill ---

async function handleBackfill(provider, dryRun = true) {
  // Find a tab for this provider.
  const providerHosts = {
    claude: 'claude.ai',
    chatgpt: 'chatgpt.com',
    gemini: 'gemini.google.com',
    grok: 'grok.com',
  };

  const host = providerHosts[provider];
  if (!host) throw new Error(`Unknown provider: ${provider}`);

  const tabs = await chrome.tabs.query({});
  const tab = tabs.find((t) => {
    try { return new URL(t.url || '').hostname.includes(host); }
    catch { return false; }
  });

  if (!tab) throw new Error(`No ${provider} tab open`);

  // Execute listing + fetching in the tab context.
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: async (prov, isDryRun) => {
      let listFn, fetchFn;

      switch (prov) {
        case 'claude': {
          const mod = await import(chrome.runtime.getURL('providers/claude.js'));
          listFn = mod.listConversations;
          fetchFn = mod.fetchConversation;
          break;
        }
        case 'chatgpt': {
          const mod = await import(chrome.runtime.getURL('providers/chatgpt.js'));
          listFn = mod.listConversations;
          fetchFn = mod.fetchConversation;
          break;
        }
        case 'gemini': {
          const mod = await import(chrome.runtime.getURL('providers/gemini.js'));
          listFn = mod.listConversations;
          fetchFn = mod.extractCurrentConversation; // DOM-only, one at a time.
          break;
        }
        case 'grok': {
          const mod = await import(chrome.runtime.getURL('providers/grok.js'));
          listFn = mod.listConversations;
          fetchFn = mod.extractCurrentConversation;
          break;
        }
        default:
          throw new Error(`Unknown provider: ${prov}`);
      }

      // List all conversations.
      const convs = await listFn();

      if (isDryRun) {
        return { dryRun: true, provider: prov, conversations: convs.length, items: convs };
      }

      // Fetch each one with a delay.
      const captured = [];
      for (let i = 0; i < convs.length; i++) {
        try {
          const data = await fetchFn(convs[i].id || convs[i].uuid);
          if (data) captured.push(data);
        } catch (err) {
          console.error(`[PersonalContext] Backfill error for ${convs[i].id || convs[i].uuid}:`, err);
        }
        // Be gentle: one request per 2 seconds.
        await new Promise((r) => setTimeout(r, 2000));
      }

      return { dryRun: false, provider: prov, captured: captured.length, items: captured };
    },
    args: [provider, dryRun],
  });

  return results[0]?.result || { ok: false, error: 'No result' };
}

console.log('[PersonalContext] Service worker ready');