// Personal Context — HTTPS ingest client.
// Sends conversations to the server. Never auto-registers on 401;
// the user must pair the browser with a code from the server CLI.

const DEFAULT_SERVER_URL = 'https://personal-context-mcp-seven.vercel.app';
const FALLBACK_SERVER_URL = 'https://mbp.tailfbd394.ts.net';

/**
 * Get the configured server URL, with fallback.
 */
async function getServerUrl() {
  const { serverUrl } = await chrome.storage.local.get('serverUrl');
  return serverUrl || DEFAULT_SERVER_URL;
}

/**
 * Get the stored device token.
 */
async function getDeviceToken() {
  const { deviceToken } = await chrome.storage.local.get('deviceToken');
  return deviceToken || null;
}

/**
 * Check if this browser is paired (has a valid token).
 */
async function isPaired() {
  const token = await getDeviceToken();
  return token !== null;
}

/**
 * Pair this browser using a one-time code from `personal-context-mcp pair-device`.
 * Returns the device token on success.
 */
async function pairDevice(pairingCode) {
  const serverUrl = await getServerUrl();
  const deviceName = `browser-${navigator.userAgent.slice(0, 60)}`;
  const resp = await fetch(`${serverUrl}/api/v1/auth/pair-device`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      pairing_code: pairingCode,
      device_name: deviceName,
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `Pairing failed: ${resp.status}`);
  }
  const { device_token, device_id } = await resp.json();
  await chrome.storage.local.set({
    deviceToken: device_token,
    deviceId: device_id,
    paired: true,
  });
  return device_token;
}

/**
 * Send a single conversation to the ingest endpoint.
 * Returns { conversation_id, status, messages_inserted, messages_skipped }.
 * Throws on 401 (not paired) — never auto-registers.
 */
async function ingestConversation(payload) {
  const serverUrl = await getServerUrl();
  const token = await getDeviceToken();

  if (!token) {
    throw new Error('Browser not paired. Use "Pair this browser" in the popup.');
  }

  const body = JSON.stringify(payload);
  const resp = await fetch(`${serverUrl}/api/v1/ingest/conversation`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body,
  });

  if (resp.status === 401) {
    // Token expired or revoked — clear it and tell the user.
    await chrome.storage.local.remove(['deviceToken', 'deviceId']);
    await chrome.storage.local.set({ paired: false });
    throw new Error('Device token expired or revoked. Re-pair the browser.');
  }

  if (!resp.ok) {
    throw new Error(`Ingest failed: ${resp.status} ${await resp.text()}`);
  }

  return resp.json();
}

export { getServerUrl, getDeviceToken, isPaired, pairDevice, ingestConversation };