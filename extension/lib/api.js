// Personal Context — HTTPS ingest client.
// Sends conversations to the server with chunked, gzip, resumable uploads.

const DEFAULT_SERVER_URL = 'https://personal-context-mcp-seven.vercel.app';
const FALLBACK_SERVER_URL = 'https://mbp.tailfbd394.ts.net';

/**
 * Get the configured server URL, with fallback.
 */
async function getServerUrl() {
  const { serverUrl, fallbackUrl } = await chrome.storage.local.get([
    'serverUrl', 'fallbackUrl',
  ]);
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
 * Register this browser as a device, obtaining a token.
 */
async function registerDevice(serverUrl) {
  const deviceName = `browser-${navigator.userAgent.slice(0, 60)}`;
  const resp = await fetch(`${serverUrl}/api/v1/auth/register-device`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ device_name: deviceName }),
  });
  if (!resp.ok) {
    throw new Error(`Device registration failed: ${resp.status}`);
  }
  const { device_token, device_id } = await resp.json();
  await chrome.storage.local.set({ deviceToken: device_token, deviceId: device_id });
  return device_token;
}

/**
 * Send a single conversation to the ingest endpoint.
 * Returns { conversation_id, status, messages_inserted, messages_skipped }.
 */
async function ingestConversation(payload) {
  const serverUrl = await getServerUrl();
  let token = await getDeviceToken();

  if (!token) {
    token = await registerDevice(serverUrl);
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
    // Token expired or revoked — re-register and retry once.
    token = await registerDevice(serverUrl);
    const retry = await fetch(`${serverUrl}/api/v1/ingest/conversation`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body,
    });
    if (!retry.ok) {
      throw new Error(`Ingest failed after re-auth: ${retry.status} ${await retry.text()}`);
    }
    return retry.json();
  }

  if (!resp.ok) {
    throw new Error(`Ingest failed: ${resp.status} ${await resp.text()}`);
  }

  return resp.json();
}

export { getServerUrl, getDeviceToken, registerDevice, ingestConversation };