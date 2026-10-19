// Personal Context — side panel script.
// Shows the project handoff text and copies it to clipboard on request.

async function init() {
  try {
    // Fetch project handoff from the server.
    const { serverUrl, deviceToken } = await chrome.storage.local.get([
      'serverUrl', 'deviceToken',
    ]);

    if (!deviceToken) {
      document.getElementById('handoff').textContent =
        'Not authenticated. Open the extension popup first.';
      return;
    }

    const resp = await fetch(
      `${serverUrl || 'https://mbp.tailfbd394.ts.net'}/api/v1/handoff`,
      {
        headers: { 'Authorization': `Bearer ${deviceToken}` },
      }
    );

    if (!resp.ok) {
      document.getElementById('handoff').textContent =
        `Could not load handoff: ${resp.status}`;
      return;
    }

    const data = await resp.json();
    document.getElementById('handoff').textContent =
      data.handoff || data.text || JSON.stringify(data, null, 2);
  } catch (err) {
    document.getElementById('handoff').textContent =
      `Error: ${err.message}`;
  }
}

document.getElementById('insertBtn').onclick = async () => {
  const text = document.getElementById('handoff').textContent;
  await navigator.clipboard.writeText(text);
  // Brief visual feedback.
  const btn = document.getElementById('insertBtn');
  btn.textContent = 'Copied!';
  setTimeout(() => { btn.textContent = 'Copy to Clipboard'; }, 1500);
};

document.addEventListener('DOMContentLoaded', init);