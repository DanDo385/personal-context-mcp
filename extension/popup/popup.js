// Personal Context — popup script.
// Shows sync status per provider, pause toggle, dead letter queue.

const providers = ['claude', 'chatgpt', 'gemini', 'grok'];
const providerNames = {
  claude: 'Claude',
  chatgpt: 'ChatGPT',
  gemini: 'Gemini',
  grok: 'Grok',
};

async function init() {
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

  // Queue info.
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
    const newState = !isPaused;
    await chrome.runtime.sendMessage({
      action: 'setPaused',
      paused: newState,
    });
    // Re-init to reflect new state.
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
    dl.textContent = `${deadLength} item(s) failed after retries. Check the server.`;
  }
}

document.addEventListener('DOMContentLoaded', init);