// Personal Context — popup script.
// Shows sync status per provider, pause toggle, pairing flow.

import { isPaired, pairDevice } from '../lib/api.js';

const providers = ['claude', 'chatgpt', 'gemini', 'grok'];
const providerNames = {
  claude: 'Claude',
  chatgpt: 'ChatGPT',
  gemini: 'Gemini',
  grok: 'Grok',
};

async function init() {
  const paired = await isPaired();

  if (!paired) {
    // Show pairing section.
    document.getElementById('pairSection').classList.remove('hidden');
    document.getElementById('mainSection').classList.add('hidden');

    const pairInput = document.getElementById('pairInput');
    const pairBtn = document.getElementById('pairBtn');
    const pairError = document.getElementById('pairError');

    pairInput.focus();

    const doPair = async () => {
      const code = pairInput.value.trim().toUpperCase();
      if (code.length !== 8) {
        pairError.textContent = 'Enter the 8-character code.';
        pairError.classList.remove('hidden');
        return;
      }

      pairBtn.disabled = true;
      pairBtn.innerHTML = '<span class="pair-spinner"></span>Pairing...';
      pairError.classList.add('hidden');

      try {
        await pairDevice(code);
        // Re-init to show main section.
        init();
      } catch (err) {
        pairError.textContent = err.message;
        pairError.classList.remove('hidden');
        pairBtn.disabled = false;
        pairBtn.textContent = 'Pair this browser';
      }
    };

    pairBtn.onclick = doPair;
    pairInput.onkeydown = (e) => {
      if (e.key === 'Enter') doPair();
    };
    return;
  }

  // Paired — show main section.
  document.getElementById('pairSection').classList.add('hidden');
  document.getElementById('mainSection').classList.remove('hidden');

  // Get status from service worker.
  const status = await chrome.runtime.sendMessage({ action: 'getStatus' });
  const { lastSync = {}, isPaused, deadLength, queueLength } = status;

  // Render provider status.
  const container = document.getElementById('providers');
  container.innerHTML = '';

  for (const provider of providers) {
    const div = document.createElement('div');
    div.className = 'provider';

    const name = document.createElement('div');
    name.className = 'provider-name';
    name.textContent = providerNames[provider];

    const statusEl = document.createElement('div');
    const last = lastSync[provider];
    if (last) {
      const time = new Date(last).toLocaleTimeString();
      statusEl.textContent = `Last sync: ${time}`;
      statusEl.className = 'provider-status success';
    } else {
      statusEl.textContent = 'No sync yet';
      statusEl.className = 'provider-status';
    }

    div.appendChild(name);
    div.appendChild(statusEl);
    container.appendChild(div);
  }

  if (queueLength > 0) {
    const queueInfo = document.createElement('div');
    queueInfo.className = 'provider-status';
    queueInfo.textContent = `${queueLength} item(s) in queue`;
    container.appendChild(queueInfo);
  }

  // Pause button.
  const pauseBtn = document.getElementById('pauseBtn');
  if (isPaused) {
    pauseBtn.textContent = 'Resume';
    pauseBtn.className = 'pause-btn paused';
  } else {
    pauseBtn.textContent = 'Pause';
    pauseBtn.className = 'pause-btn';
  }

  pauseBtn.onclick = async () => {
    await chrome.runtime.sendMessage({
      action: 'setPaused',
      paused: !isPaused,
    });
    init();
  };

  // Capture button.
  document.getElementById('captureBtn').onclick = async () => {
    const [tab] = await chrome.tabs.query({
      active: true,
      currentWindow: true,
    });
    if (tab) {
      chrome.tabs.sendMessage(tab.id, { action: 'capture' }).catch(() => {
        alert('Could not capture. Make sure you are on a supported chat page.');
      });
      window.close();
    }
  };

  // Dead letter queue.
  if (deadLength > 0) {
    const dl = document.getElementById('deadLetter');
    dl.className = 'dead-letter';
    dl.textContent = `${deadLength} item(s) failed. Check the server.`;
  }
}

document.addEventListener('DOMContentLoaded', init);